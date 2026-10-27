# 平台账号

首页提供登录、注册和退出入口。默认演示账号显示在账号区和登录窗口下方：

| 用户名 | 密码 |
|---|---|
| `admin` | `admin123` |

注册成功后自动登录。用户名为 3–24 位字母、数字或下划线，不区分大小写，密码为 8–128 位。
重复用户名会提示更换或登录。未登录点击模块会先打开登录窗口，成功后回到所选模块；
直接打开模块地址也会转到首页登录。刷新页面会向服务端检查当前会话。

## 保存方式

- 数据库存放在 `UAV_DATA_DIR/uav-inspection-backend/auth/accounts.sqlite3`。
  未指定数据目录时遵循 `deployment_paths.py` 的原有规则。Docker 命名卷会持久化账号。
- 使用 Python 标准库 SQLite，无需增加安装依赖。账号和会话均保存在服务端。
- 密码使用随机盐与 PBKDF2-HMAC-SHA256（300,000 轮），不保存明文密码。
- 浏览器只持有 HttpOnly、SameSite=Lax 会话 Cookie，HTTPS 下设置 Secure；
  数据库保存会话令牌的 SHA-256 摘要，服务端有效期为 24 小时。
- 退出时撤销服务端会话并清除 Cookie。替换当前浏览器登录身份会撤销其旧会话。
- `/uav/` 挂载与根路径均可使用。重建镜像不应把运行时账号数据库复制进代码或镜像；
  `.gitignore` 与 `.dockerignore` 已排除默认运行目录。

## 接口与范围

`/api/v1/auth` 下提供 `POST /register`、`POST /login`、`GET /me`、`POST /logout`。
前端业务路由要求登录；现有业务 API、媒体与 WebSocket 的访问方式保持兼容。
本次账号功能没有引入角色授权或按用户隔离业务数据，现有业务素材仍属于同一平台工作区。
默认账号仅在缺失时初始化，不覆盖已有同名账号的密码。注册用户与运行数据不进入 PR。

## 验证

```bash
cd app/uav-inspection-backend
python -m unittest discover -s tests -p test_accounts.py
```

8 项服务端测试覆盖默认登录、密码与令牌摘要、错误凭据、注册持久化、重复用户名、
输入校验、服务端过期与退出撤销、子路径 Cookie 和 HTTPS Secure。
浏览器实测覆盖默认登录、新用户注册、密码确认、密码显隐、退出、刷新、模块回跳和窄屏布局。
