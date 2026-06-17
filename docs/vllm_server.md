# vLLM 服务部署指南

档案分类系统的 LLM 推理已外置到独立的 vLLM OpenAI 兼容服务。客户端只通过 HTTP 调用，不在本进程加载模型。

> 本文命令面向真实 GPU 服务器。当前会话若为非可执行环境,只能静态维护文档,不能启动 vLLM 或执行健康检查。

## 1. 模型位置

模型已下载到：

```
~/.cache/huggingface/hub/Qwen3-32B-AWQ
```

AWQ 4bit 量化，单卡 ≥ 24 GB 显存即可；80 GB A100/H100 可顺畅跑 32k 上下文。

## 2. 启动 vLLM 服务

在 GPU 服务器上执行（建议在 tmux / screen 或 systemd 里常驻）：

```bash
vllm serve ~/.cache/huggingface/hub/Qwen3-32B-AWQ \
  --served-model-name qwen3-32b-awq \
  --host 127.0.0.1 \
  --port 8000 \
  --quantization awq_marlin \
  --dtype auto \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.6
```

> **与 PaddleOCR 同卡时务必调低。** 本项目每个并发跑批 worker 会各自在同一张
> GPU 上加载一份 PaddleOCR(`OCR_USE_GPU=True`)。vLLM 默认会一次性圈走 ~90%
> 显存,OCR 就没余量了(实测 A6000 48GB 上并发 1 即 ~97%)。把
> `--gpu-memory-utilization` 降到 **0.6**(48GB 卡约腾出 14GB)给 OCR 留地方,
> 即可支撑小并发跑批。代价仅是 KV cache 变小(短文本元数据抽取无感)。
> 若 vLLM 独占整卡、OCR 走 CPU,则可调回 0.90。

关键参数说明：

| 参数 | 作用 |
|---|---|
| `--served-model-name qwen3-32b-awq` | 客户端 `Config.LLM_MODEL_NAME` 必须与此一致 |
| `--quantization awq_marlin` | Marlin kernel 跑 AWQ，比原生 awq 快 ~30% |
| `--max-model-len 8192` | KV cache 上限；大于客户端 prompt+ocr_text+max_tokens 即可 |
| `--gpu-memory-utilization 0.6` | 与同卡 PaddleOCR 共存的推荐值;vLLM 独占整卡可调到 0.90~0.95 |
| `--host 0.0.0.0` | 允许跨机访问，仅本机调用可改 `127.0.0.1` |

多卡场景追加：

```bash
  --tensor-parallel-size 2       # 2 张卡做张量并行
```

显存吃紧时可降：

```bash
  --max-model-len 4096 \
  --gpu-memory-utilization 0.85 \
  --enforce-eager                 # 禁用 CUDA graph 省一点显存，代价是吞吐降低
```

## 3. 健康检查

服务起来后：

```bash
# 列出已加载模型
curl http://localhost:8000/v1/models

# 冒烟测试
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3-32b-awq",
    "messages": [{"role":"user","content":"只输出 JSON: {\"ok\":true}"}],
    "response_format": {"type": "json_object"},
    "max_tokens": 32,
    "chat_template_kwargs": {"enable_thinking": false}
  }'
```

返回应包含 `"content":"{\"ok\":true}"` 形式的 JSON。

## 4. 客户端配置

客户端侧通过环境变量覆写（不改就用 `config/config.py` 默认值）：

```bash
export LLM_BASE_URL="http://<server-ip>:8000/v1"
export LLM_MODEL_NAME="qwen3-32b-awq"   # 必须等于 --served-model-name
export LLM_API_KEY="EMPTY"              # vLLM 默认不校验
export LLM_REQUEST_TIMEOUT="300"        # 秒；32B 在长 prompt 下可能慢
export LLM_ENABLE_THINKING="false"      # Qwen3 思考模式；JSON 场景保持关闭
```

然后：

```bash
python main.py
```

## 5. 常见问题

**启动时 OOM**：降低 `--max-model-len` 或 `--gpu-memory-utilization`。AWQ 版 32B 权重 ~19 GB，其余显存全部用于 KV cache。

**响应中带 `<think>...</think>` 前缀**：Qwen3 的思考模式未关闭。检查客户端有没有传 `chat_template_kwargs={"enable_thinking": false}`（代码默认已传）。必要时在用户 prompt 末尾加 `/no_think` 兜底。

**`response_format` 报 unsupported**：vLLM 版本过旧，升级到 `vllm>=0.6.3`。或把 `response_format` 换成 `extra_body={"guided_json": <schema>}` 做严格 schema 约束。

**连接超时**：确认服务所在主机防火墙开放 8000 端口；跨机访问必须 `--host 0.0.0.0`。

**首次请求极慢**：vLLM 启动后首次请求会编译 CUDA graph，几十秒到一两分钟都正常，后续稳定。加 `--enforce-eager` 可避免但会损失吞吐。

**停止服务**：优先在 tmux/screen/systemd 中正常 `Ctrl-C` 或 `systemctl stop`；临时排障时才使用 `sudo pkill -f vllm`。
