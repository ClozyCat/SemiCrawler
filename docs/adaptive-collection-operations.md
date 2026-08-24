# 自适应采集运维与发布门槛

## 日常验证

脱网回归默认执行：

```powershell
uv run pytest
npm run build
```

固定 HTML 样本位于 `tests/fixtures/adaptive`，不依赖公网。外部站点只读回归必须显式开启：

```powershell
$env:SEMICRAWLER_EXTERNAL = "1"
uv run pytest -m external
```

外部检查最多读取两个页面、最多 40 条记录，不执行搜索、登录、表单写操作或浏览器持久化。站点不可达、429、503 或 robots 临时异常应记录为外部环境失败，不修改来源规则。

## 指标

`GET /api/metrics/collection` 提供每来源运行指标：访问方式、内容类型、耗时、页数、发现、保存、去重、失败、规则修复次数、模型调用次数和估算成本。发布前检查最近三次运行：

- `failed == 0`，或失败均有可解释日志；
- `deduplicated` 不应持续异常增长；
- 页数和条目数受 `max_pages/max_items` 限制；
- `rule_repairs` 最多一次/任务；
- 浏览器只出现在显式启用且 HTTP 探测失败的来源；
- 外部回归不出现跨域或私网访问记录。

## 失败处理

任务失败时先查看 `/api/tasks/{id}/logs`，确认来源、transport、规则版本、指纹和停止原因。页面指纹变化会触发一次自动重新探测；重新探测仍失败则保留旧规则和原始日志，不进入无限重试。

Playwright 未安装或浏览器启动失败时，任务会明确记录“浏览器执行器未启用/启动失败”。安装浏览器后可对来源执行“重新探测”，无需修改历史原文。

## 回滚

来源规则每次保存都会写入 `source_versions`。回滚时将目标版本的 `config_json` 写回 `sources`，再执行一次试抓取和双页验证；不要直接删除 `learned_profile` 以外的字段。数据库迁移由启动时事务化执行，失败会回滚并保留原表。

发布发现重复、漏字段、无限分页或越权访问时：立即停用来源，保留任务与日志，恢复上一个已验证 `source_versions` 配置，运行固定样本回归后再重新启用。
