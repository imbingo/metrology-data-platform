# 半导体量测数据平台 Demo

## 运行方式

```bash
python metrology_app.py
```

打开浏览器：

```text
http://127.0.0.1:8000
```

默认账号：

```text
admin
```

默认密码：

```text
admin123
```

## 已包含功能

- 登录界面
- SQLite 自动建库
- Dashboard
- Lot 查询
- SPC 趋势图
- OOC/OOS 告警
- 设备状态
- CSV 粘贴导入

## 说明

这是一个零依赖单文件 Demo，适合先跑通业务原型。
正式产线版建议改成 Vue + FastAPI/Spring Boot + PostgreSQL，并接入公司 AD/LDAP、MES 接口、设备数据采集服务。
