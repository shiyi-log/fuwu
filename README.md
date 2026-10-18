# fuwu

项目仓库，目标技术栈为 Django + Django REST Framework。

## 项目记录

- [项目台账](PROJECT_LEDGER.md)
- [API 接口文档](docs/API.md)
- [版本记录](docs/VERSION_HISTORY.md)

## 自动化

- `CI`：检查 Markdown 文档、Python 源码、Django 配置、迁移和测试。
- `CD`：在 `vMAJOR.MINOR.PATCH` 标签上构建源码包并创建 GitHub Release；手动触发默认只做验证，显式选择发布后才创建 Release。
