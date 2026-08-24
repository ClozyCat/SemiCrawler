# 自适应网页采集实施计划

## 1. 目标与边界

将信息源接入从“人工填写 URL 正则和 CSS 选择器”改为“用户提供站点或栏目 URL，系统自动探测、验证并保存采集规则”。LLM 负责在页面结构不明确时选择访问动作、识别数据区域和生成候选规则；所有网络请求仍由后端受控执行。

本阶段目标：

- 用户新增来源时只需填写名称、入口 URL 和可选的数据含义提示。
- 系统自动识别 RSS/Atom、Sitemap、普通链接列表、iframe、HTML 表格和 POST 表单分页。
- 普通采集复用已经验证的规则，不对每一页重复调用 LLM。
- 页面结构变化导致规则失效时，自动重新探测一次并保留完整日志。
- 同时支持“新闻文章”和“公示表格记录”，并复用现有关键词过滤、去重、`RawArticle -> StructuredRecord` 流程。
- 兼容已有 v1 JSON 来源配置，现有两个内置信息源无需立即迁移。

明确不做：

- 不绕过登录、验证码、付费墙或访问控制。
- 不允许 LLM 任意访问内网、文件协议或未授权域名。
- 不以模型厂商的联网搜索代替指定站点的完整采集。
- 首版不支持需要模拟复杂业务操作才能生成数据的站点。

## 2. 测试站勘察结论

勘察日期：2026-08-24。使用真实浏览器观察页面、iframe、表单和网络请求，并用普通 HTTP 请求验证关键数据页可直接访问。

### 2.1 全国投资项目在线审批监管平台

入口：`https://new.tzxm.gov.cn/bsdt/#gxframe`

- 外层页面包含“项目办理结果公示”iframe。
- iframe 首屏实际加载 `https://new.tzxm.gov.cn/tzpt/statics/html/announce/1.shtml`。
- 点击下一页后加载 `/tzpt/statics/html/announce/2.shtml`；页面显示共 20 页。
- 数据是服务端渲染的 HTML 表格，无需浏览器执行 JavaScript即可下载具体分页。
- 表格字段为项目名称、项目代码、审批事项、审批时间、审批结果。
- 每行没有独立详情 URL，不能继续把文章 URL 当作唯一记录标识。
- `/robots.txt` 当前返回 404。采集器应记录“未提供 robots.txt”，继续遵守限速和站点条款，不将 404 误报为禁止采集。

建议自动学习出的规则：

```json
{
  "content_kind": "table_records",
  "transport": "http",
  "entry": "https://new.tzxm.gov.cn/tzpt/statics/html/announce/1.shtml",
  "pagination": {
    "kind": "url_template",
    "template": "https://new.tzxm.gov.cn/tzpt/statics/html/announce/{page}.shtml",
    "start_page": 1
  },
  "fields": {
    "title": "项目名称",
    "project_code": "项目代码",
    "approval_item": "审批事项",
    "published_at": "审批时间",
    "approval_result": "审批结果"
  },
  "date_order": "descending"
}
```

### 2.2 江苏省投资项目审批监管平台

入口：`https://tzxm.fzggw.jiangsu.gov.cn/portalopenPublicInformation.do?method=queryExamineAll`

- 首屏即为服务端渲染 HTML 表格，无需浏览器渲染。
- 页面显示约 144 万条记录、72003 页，每页 20 条；必须依赖日期截止条件提前停止，禁止默认全量遍历。
- 翻页向同一 URL 发起 POST，请求体为：

```text
pageSize=20&pageNo=2&apply_project_name=&projectInfo.areaDetialCode=&projectInfo.projectAddress=&projectInfo.areaDetial=&projectInfo.industryId=&projectInfo.industry=
```

- 表格字段为项目代码/项目名称、审批事项、审批部门、部门区划、审批结果、批复文号、审批时间。
- 每行同样没有独立详情 URL。
- `/robots.txt` 当前返回站点的“页面不存在”HTML，而不是 robots 文本。robots 解析器需要按状态码和 Content-Type 判断是否为有效规则文件。

建议自动学习出的规则：

```json
{
  "content_kind": "table_records",
  "transport": "http",
  "entry": "https://tzxm.fzggw.jiangsu.gov.cn/portalopenPublicInformation.do?method=queryExamineAll",
  "pagination": {
    "kind": "form_post",
    "method": "POST",
    "page_field": "pageNo",
    "page_size_field": "pageSize",
    "page_size": 20,
    "static_fields": {
      "apply_project_name": "",
      "projectInfo.areaDetialCode": "",
      "projectInfo.projectAddress": "",
      "projectInfo.areaDetial": "",
      "projectInfo.industryId": "",
      "projectInfo.industry": ""
    }
  },
  "fields": {
    "title": "项目名称",
    "project_code": "项目代码",
    "approval_item": "审批事项",
    "approval_authority": "审批部门",
    "organization": "部门区划",
    "approval_result": "审批结果",
    "approval_document": "批复文号",
    "published_at": "审批时间"
  },
  "date_order": "descending"
}
```

## 3. 目标架构

```text
来源 URL
   |
   v
安全抓取器 -> 页面观察对象 -> 确定性探测器
                              | 成功
                              v
                         候选采集规则
                              |
                   不明确     | 样本验证
页面观察对象 -> LLM 探测代理 -+
                              |
                              v
                     已验证规则/版本/指纹
                              |
                              v
                HTTP 执行器或浏览器执行器
                              |
                              v
                 ArticleItem / RecordItem
                              |
                              v
                         RawArticle
                              |
                              v
                    现有 LLM 结构化流程
```

职责划分：

- `SafeFetcher`：URL 校验、DNS/IP 检查、robots、重定向、限速、响应大小和超时。
- `PageInspector`：生成精简观察对象，包括标题、元数据、iframe、表单、表头、候选链接、分页控件和必要的事件信息。
- `DeterministicDetector`：优先识别 feed、sitemap、JSON-LD、`article`、表格、iframe 和常见分页。
- `ProbeAgent`：只有确定性探测置信度不足时才调用 LLM，最多执行有限轮“观察 -> 决策 -> 后端访问 -> 新观察”。
- `ProfileValidator`：抓取至少两个样本页，验证字段完整率、分页是否变化、日期是否可解析、记录键是否稳定。
- `CollectionExecutor`：只执行已验证规则；失败时允许重新探测一次。
- `ArticleExtractor`：通用正文提取，顺序为 JSON-LD、OpenGraph/语义标签、正文算法、LLM 兜底。
- `RecordExtractor`：根据表头语义和已学习字段映射输出表格记录。

建议新增模块：

```text
app/collection/
  fetcher.py
  inspection.py
  profiles.py
  probing.py
  validation.py
  executors.py
  article_extractor.py
  record_extractor.py
  safety.py
```

现有 `app/crawler.py` 保留任务编排、关键词过滤、日期过滤和统计职责，逐步把页面访问与解析委托给上述模块。

## 4. LLM 探测代理协议

当前 OpenAI 兼容接口并不保证所有模型都支持原生 tool calling。首版使用“结构化动作 JSON + 后端执行”的兼容协议，不依赖模型厂商专属联网功能。

允许动作：

- `inspect_url`：访问同域或显式允许域名的 HTTP(S) URL。
- `inspect_iframe`：读取观察对象中列出的 iframe。
- `inspect_form`：以 GET/POST 提交无敏感字段的分页或查询表单。
- `propose_profile`：输出候选采集规则。
- `stop`：无法安全、稳定接入时停止并说明原因。

每轮输入仅包含精简后的页面观察，不发送完整脚本、图片或超大 DOM。代理最多 4 轮、最多访问 8 个页面、最多跨 1 个显式批准的关联域名。模型输出使用 Pydantic Schema 校验；非法动作不会执行，并向模型返回一次修复机会。

LLM 只参与以下场景：

- 新来源首次接入且确定性规则不能可靠判定。
- 已保存规则因页面指纹变化或连续解析失败而需要修复。
- 通用正文提取器无法得到有效正文。

正常批量采集不调用探测 LLM。目标是每个来源首次接入不超过 3 次模型请求，稳定运行时每页 0 次探测模型请求。

## 5. 数据模型与迁移

现有 `RawArticle.canonical_url` 是全局唯一，但两个测试站的多条记录共享同一个公示页 URL，因此需要迁移：

- 新增 `source_item_key`：来源内部稳定记录键。
- 新增唯一约束 `(source_id, source_item_key)`。
- 移除 `canonical_url` 的全局唯一约束；该字段继续保存用户可打开的真实来源 URL。
- 新增 `content_kind`，枚举值先支持 `article`、`table_record`。
- 新增 `raw_payload_json`，保存表格原始字段，供审核和未来重新结构化使用。
- 旧数据以规范化后的 `canonical_url` 回填 `source_item_key`，`content_kind` 回填为 `article`。

记录键规则：

- 有独立文章 URL：规范化 URL。
- 全国公示：项目代码 + 审批事项 + 审批时间 + 审批结果。
- 江苏公示：项目代码 + 审批事项 + 审批部门 + 批复文号 + 审批时间。
- 缺少业务主键时：规范化字段集合的 SHA-256；不得使用页码或行号。

表格记录映射到现有字段：

- `title`：项目名称。
- `published_at`：审批时间。
- `published_text`：页面原始日期文本。
- `body`：按“字段名：值”生成的可读文本，供关键词过滤和 LLM 结构化。
- `canonical_url`：真实公示入口或可复现的查询 URL。
- `raw_payload_json`：全部原始列，不因现有结构化字段不足而丢失数据。

SQLite 迁移必须采用可回滚的表重建事务，并在迁移前后验证记录数、外键和唯一约束。不能只使用现有的 `ALTER TABLE ADD COLUMN` 逻辑处理唯一约束变化。

## 6. 来源配置 v2

保留现有 v1 配置解析器，新增 v2：

```json
{
  "version": 2,
  "entry_urls": ["https://example.com/public"],
  "mode": "auto",
  "allowed_hosts": ["example.com"],
  "content_hint": "公开项目审批结果",
  "limits": {
    "rate_limit_per_minute": 12,
    "timeout_seconds": 20,
    "max_pages": 100,
    "max_items": 5000
  },
  "learned_profile": null
}
```

`learned_profile` 只能由后端生成，包含：

- 探测方式和置信度。
- 入口、请求方法、分页规则和字段映射。
- 页面结构指纹。
- 样本验证结果。
- 模型名称、生成时间和最后验证时间。
- 允许访问的主机列表。

用户可以查看探测摘要、重新探测和切换到高级 JSON 编辑，但默认界面不要求填写选择器或正则。

## 7. 安全和运行限制

- 仅允许 `http`、`https`；拒绝带用户名密码的 URL。
- DNS 解析后拒绝 loopback、私网、链路本地、保留地址和云元数据地址；每次重定向后重新校验。
- 默认只允许与入口同主机；iframe 或表单目标跨域时必须进入待确认状态，不能由网页或 LLM 自行扩大域名范围。
- 仅支持 GET 和普通表单 POST；禁止文件上传、认证表单和任意请求头注入。
- 单响应默认不超过 5 MiB，精简 DOM 不超过 100 KiB，文章正文不超过现有模型输入上限。
- 每来源分别限速并设置并发上限；429/503 使用有上限的指数退避。
- 严格遵守有效 robots.txt；robots 不存在或格式错误时记录审计事件并采用保守限速。
- 网页内容一律视为不可信数据。系统提示明确禁止执行页面中的指令、泄露配置或修改访问边界。
- 浏览器执行器默认关闭，仅在 HTTP 观察不到数据且来源允许时启用；禁止下载和持久化登录状态。

## 8. 分阶段实施

### 阶段 A：数据兼容与安全抓取基础

交付：

- `RawArticle` 数据迁移和新字段。
- v1/v2 来源配置判别与兼容读取。
- `SafeFetcher`、重定向校验、响应限制和更准确的 robots 处理。
- 统一 `PageResponse`、`ArticleItem`、`RecordItem` 数据契约。

完成标准：现有测试全部通过；旧数据库升级后记录数不变；同一公示 URL 可保存多条不同记录；重复运行不会重复入库。

### 阶段 B：确定性自动探测与表格采集

交付：

- iframe、HTML 表格、表头、GET/POST 表单和分页识别。
- URL 模板分页、链接分页和表单分页执行器。
- 字段语义映射、记录键、日期倒序停止。
- 自动规则的双页样本验证和页面指纹。

完成标准：不调用 LLM、不给 CSS 选择器时，两个测试站均能自动形成候选规则并抓取至少前两页；任务设置日期后在遇到更早日期的页面及时停止。

### 阶段 C：文章通用抽取与 LLM 探测代理

交付：

- RSS/Atom、Sitemap、JSON-LD、OpenGraph 和正文算法。
- `ProbeDecision`、`CollectionProfile` Pydantic Schema。
- 有限轮代理编排、模型修复重试、规则验证和失败解释。
- 页面结构变化后的单次自动修复。

完成标准：仅提供 URL 可接入通用新闻站；模型无法输出合法规则时安全失败；任何 LLM 输出都不能突破 URL 和请求限制。

### 阶段 D：浏览器兜底与管理界面

交付：

- Playwright 浏览器执行器，仅处理 JavaScript 渲染页面。
- 来源表单改为基础模式：名称、入口 URL、内容提示、限速。
- 探测进度、样本预览、规则来源、置信度、重新探测和高级配置。
- 任务日志增加访问方式、停止原因、规则版本和自动修复记录。

完成标准：移动端和桌面端界面无回归；浏览器执行器未启用时给出明确原因；已有高级配置仍可编辑和试抓取。

### 阶段 E：外部回归与发布门槛

交付：

- 两站外部只读回归测试。
- 固定 HTML 样本和脱网测试。
- 成本、延迟、错误率和规则修复次数统计。
- 运维说明、失败处理和回滚说明。

完成标准：满足第 10 节验收标准，且连续三次外部回归未出现重复、漏字段、无限分页或越权访问。

## 9. 测试设计

### 9.1 单元测试

- URL、DNS、重定向和 SSRF 拦截。
- robots 200、404、错误 HTML和明确 Disallow。
- iframe、表格、表单、分页和日期顺序识别。
- 中文表头到标准字段的映射。
- 记录键跨页稳定性及不同审批事项不冲突。
- LLM 动作 Schema、轮数限制、域名限制和非法动作修复。
- 日期截止、关键词过滤、内容哈希和来源键去重。

### 9.2 固定样本集成测试

保存两个测试站的脱敏/裁剪 HTML 样本：

- 全国站 `announce/1.shtml`、`announce/2.shtml` 和外层 iframe 页面。
- 江苏站 GET 第 1 页和 POST 第 2 页响应。

固定样本用于日常 CI，不依赖公网和当天数据。断言表头、行数、分页、日期、稳定键和生成的 `RawArticle.body/raw_payload_json`。

### 9.3 外部只读回归

使用 `pytest -m external` 单独运行，默认 CI 不执行：

- 每站最多读取 2 页，不执行搜索、登录或高频请求。
- 请求间隔至少 3 秒，总请求数设置硬上限。
- 不断言具体项目名称，只断言字段完整率、日期格式、页间变化和非重复记录键。
- 站点不可达、临时 429/5xx 记为外部环境失败，不污染确定性单元测试。

### 9.4 模型测试

- CI 使用固定模型输出测试代理编排，不消耗真实 API。
- 手工发布检查可使用已配置模型执行一次真实探测。
- 真实模型测试断言访问边界和生成规则的可验证性，不断言逐字输出。

## 10. 两站最终验收标准

全国站：

- 从用户给出的外层 URL 自动发现 iframe 数据页。
- 自动识别静态 URL 分页并抓取第 1、2 页。
- 每行得到项目名称、项目代码、审批事项、审批日期和审批结果。
- 相同入口下不同记录均可保存；重复运行不新增重复记录。

江苏站：

- 自动识别服务端 HTML 表格和 POST 翻页参数。
- 每行得到项目名称、项目代码、审批事项、审批部门、部门区划、审批结果、批复文号和审批日期。
- 日期起点为当天时，不得继续扫描历史 72003 页。
- 最多两页的验收任务结束后，记录键无重复，字段完整率达到 95% 以上。

通用验收：

- 新增来源的默认流程不要求用户提供 CSS 选择器或 URL 正则。
- 所有保存记录保留真实来源入口和原始字段证据。
- 正常采集阶段不调用探测 LLM；页面规则失效最多自动修复一次。
- 不访问私网、未批准跨域、登录页或验证码后的内容。
- 旧来源、旧任务、结构化、导出和前端历史查看无回归。

## 11. 建议合并顺序

1. 数据迁移、v2 Schema、抓取安全层和测试。
2. 页面观察对象、确定性探测、表格记录执行器和两站固定样本。
3. LLM 探测代理、规则验证、缓存与自动修复。
4. 通用文章抽取、RSS/Sitemap 和浏览器兜底。
5. 来源管理界面、外部回归、指标与发布文档。

每一步均应保持现有 v1 来源可运行，避免一次性替换 `crawler.py`。阶段 B 完成后，这两个指定站点即应具备可用采集能力；后续阶段扩展的是通用性和自动修复能力。
