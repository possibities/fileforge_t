"""人员/用户管理 CLI。

用于在 Web 页面上线前完成 PostgreSQL 账号初始化、人员管理和登录校验。
输出始终为 JSON。
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import sys
from typing import Any, Callable, Optional

from utils._cli_common import add_database_url_arg, resolve_database_url

logger = logging.getLogger("user_admin")


def _print_json(payload: Any) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    sys.stdout.write("\n")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="user_admin",
        description="人员/用户管理 CLI(JSON 输出)",
    )
    add_database_url_arg(parser)
    sub = parser.add_subparsers(dest="resource", required=False)

    p_roles = sub.add_parser("roles", help="角色/权限")
    sub_roles = p_roles.add_subparsers(dest="verb", required=False)
    p_roles_init = sub_roles.add_parser("init", help="初始化内置角色和权限")
    p_roles_init.set_defaults(func=_cmd_roles_init)

    p_orgs = sub.add_parser("orgs", help="单位")
    sub_orgs = p_orgs.add_subparsers(dest="verb", required=False)
    p_org_create = sub_orgs.add_parser("create", help="创建单位")
    p_org_create.add_argument("--name", required=True)
    p_org_create.set_defaults(func=_cmd_orgs_create)

    p_users = sub.add_parser("users", help="用户")
    sub_users = p_users.add_subparsers(dest="verb", required=False)
    p_users_create = sub_users.add_parser("create", help="创建用户")
    p_users_create.add_argument("--username", required=True)
    p_users_create.add_argument("--password", required=True)
    p_users_create.add_argument("--display-name", default=None)
    p_users_create.add_argument("--organization-id", type=int, default=None)
    p_users_create.add_argument("--role", action="append", dest="roles", default=[])
    p_users_create.set_defaults(func=_cmd_users_create)

    p_users_list = sub_users.add_parser("list", help="列出用户")
    p_users_list.set_defaults(func=_cmd_users_list)

    p_users_disable = sub_users.add_parser("disable", help="禁用用户")
    p_users_disable.add_argument("--username", required=True)
    p_users_disable.set_defaults(func=_cmd_users_disable)

    p_users_reset = sub_users.add_parser("reset-password", help="重置密码")
    p_users_reset.add_argument("--username", required=True)
    p_users_reset.add_argument("--password", required=True)
    p_users_reset.set_defaults(func=_cmd_users_reset_password)

    p_login = sub.add_parser("login", help="校验用户名密码")
    p_login.add_argument("--username", required=True)
    p_login.add_argument("--password", required=True)
    p_login.set_defaults(func=_cmd_login)

    return parser


def _cmd_roles_init(args, session) -> int:
    from infrastructure.db import accounts

    accounts.ensure_builtin_roles(session)
    session.commit()
    _print_json({"ok": True})
    return 0


def _cmd_orgs_create(args, session) -> int:
    from infrastructure.db import accounts

    org = accounts.create_organization(session, name=args.name)
    session.commit()
    _print_json({"id": org.id, "name": org.name, "status": org.status})
    return 0


def _cmd_users_create(args, session) -> int:
    from infrastructure.db import accounts

    user = accounts.create_user(
        session,
        username=args.username,
        password=args.password,
        display_name=args.display_name,
        organization_id=args.organization_id,
        role_codes=args.roles,
    )
    session.commit()
    _print_json(
        {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "status": user.status,
        }
    )
    return 0


def _cmd_users_list(args, session) -> int:
    from infrastructure.db import accounts

    rows = [dataclasses.asdict(row) for row in accounts.list_users(session)]
    _print_json({"items": rows, "total": len(rows)})
    return 0


def _cmd_users_disable(args, session) -> int:
    from infrastructure.db import accounts

    user = accounts.disable_user(session, username=args.username)
    session.commit()
    _print_json({"id": user.id, "username": user.username, "status": user.status})
    return 0


def _cmd_users_reset_password(args, session) -> int:
    from infrastructure.db import accounts

    user = accounts.reset_password(
        session,
        username=args.username,
        new_password=args.password,
    )
    session.commit()
    _print_json({"id": user.id, "username": user.username, "password_reset": True})
    return 0


def _cmd_login(args, session) -> int:
    from infrastructure.db import accounts

    user = accounts.authenticate_user(
        session,
        username=args.username,
        password=args.password,
    )
    session.commit()
    if user is None:
        _print_json({"authenticated": False})
        return 4
    _print_json(
        {
            "authenticated": True,
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
        }
    )
    return 0


def run(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2

    if not getattr(args, "resource", None):
        parser.print_usage(file=sys.stderr)
        sys.stderr.write("error: missing subcommand\n")
        return 2

    func: Optional[Callable] = getattr(args, "func", None)
    if func is None:
        sys.stderr.write("error: missing subcommand handler\n")
        return 2

    database_url = resolve_database_url(args)
    if not database_url:
        sys.stderr.write("error: DATABASE_URL not set\n")
        return 2

    try:
        from infrastructure.db.engine import (
            check_connectivity,
            dispose_engine,
            make_engine,
            make_session_factory,
        )
    except ImportError as exc:
        sys.stderr.write(
            f"error: missing database dependency: {exc}. "
            "请 pip install -r requirements/db.txt\n"
        )
        return 3

    engine = None
    try:
        engine = make_engine(database_url)
        check_connectivity(engine)
    except Exception as exc:
        sys.stderr.write(f"error: database connection failed: {exc}\n")
        if engine is not None:
            dispose_engine(engine)
        return 3

    session_factory = make_session_factory(engine)
    try:
        with session_factory() as session:
            return func(args, session)
    except ValueError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    except Exception as exc:
        logger.exception("unhandled error in user_admin: %s", exc)
        sys.stderr.write(f"error: {exc}\n")
        return 9
    finally:
        dispose_engine(engine)


def main() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
    sys.exit(run())


if __name__ == "__main__":
    main()

