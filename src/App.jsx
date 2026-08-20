import { useCallback, useEffect, useMemo, useState } from 'react'
import { useRef } from 'react'
import {
  Activity, BookOpen, CalendarDays, CheckCircle2, ChevronDown,
  Database, Download, ExternalLink, FileJson, FileSpreadsheet, History,
  LayoutDashboard, ListFilter, LoaderCircle, Menu, Plus, RefreshCw, Search,
  Settings, SlidersHorizontal, X, Eye, FlaskConical, Save, Trash2, Tags, Upload, Network, Maximize2, Minimize2,
} from 'lucide-react'
import { api } from './api'

const DEFAULT_DATE = '2026-08-01'
const NAV = [
  { id: 'dashboard', label: '采集工作台', icon: LayoutDashboard },
  { id: 'history', label: '数据归档', icon: History },
  { id: 'analytics', label: '关联洞察', icon: Network },
  { id: 'keywords', label: '关键词配置', icon: Tags },
  { id: 'sources', label: '信息源管理', icon: BookOpen },
  { id: 'settings', label: 'API配置', icon: Settings },
]
const EMPTY_FORM = {
  name: '', base_url: '', enabled: true,
  config: JSON.stringify({
    entry_urls: ['https://example.com/news'], article_url_pattern: '/news/',
    selectors: { list_links: 'article a', title: 'h1', date: '.publish-date', content: '.content' },
    request: { rate_limit_per_minute: 20, timeout_seconds: 20 },
  }, null, 2),
}

function formatTime(value) {
  if (!value) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'medium', timeStyle: 'short', timeZone: 'Asia/Shanghai',
  }).format(new Date(value))
}

const TASK_STATUS_LABELS = {
  queued: '排队中',
  running: '执行中',
  completed: '已完成',
  completed_with_errors: '部分完成',
  failed: '失败',
}

function taskStatusLabel(status) {
  return TASK_STATUS_LABELS[status] || status
}

function Modal({ title, children, onClose, wide = false }) {
  return <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
    <section className={`modal ${wide ? 'modal-wide' : ''}`} role="dialog" aria-modal="true" aria-label={title} onMouseDown={(event) => event.stopPropagation()}>
      <header className="modal-head"><h2>{title}</h2><button className="icon-btn" onClick={onClose} aria-label="关闭"><X /></button></header>
      {children}
    </section>
  </div>
}

function SourceForm({ source, onClose, onSaved }) {
  const [form, setForm] = useState(source ? { ...source, config: JSON.stringify(source.config, null, 2) } : EMPTY_FORM)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [preview, setPreview] = useState(null)
  const submit = async (event) => {
    event.preventDefault(); setError(''); setSaving(true)
    try {
      const payload = { name: form.name.trim(), base_url: form.base_url.trim(), enabled: form.enabled, config: JSON.parse(form.config) }
      const saved = source ? await api.updateSource(source.id, payload) : await api.addSource(payload)
      onSaved(saved); onClose()
    } catch (err) { setError(err instanceof SyntaxError ? '配置不是有效的 JSON' : err.message) }
    finally { setSaving(false) }
  }
  const readFile = (event) => {
    const file = event.target.files?.[0]
    if (!file) return
    const reader = new FileReader(); reader.onload = () => setForm((old) => ({ ...old, config: String(reader.result) })); reader.readAsText(file)
  }
  const test = async () => {
    setTesting(true); setError(''); setPreview(null)
    try { setPreview(await api.testSource({ base_url: form.base_url.trim(), config: JSON.parse(form.config) })) }
    catch (err) { setError(err instanceof SyntaxError ? '配置不是有效的 JSON' : err.message) }
    finally { setTesting(false) }
  }
  return <Modal title={source ? '编辑信息源' : '添加信息源'} onClose={onClose} wide>
    <form onSubmit={submit}>
      <div className="modal-body form-grid">
        <label className="field"><span>来源名称</span><input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="例如：某开发区官网" /></label>
        <label className="field"><span>站点地址</span><input required type="url" value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} placeholder="https://example.com" /></label>
        <label className="field full"><span>JSON 来源配置</span><textarea rows="14" value={form.config} onChange={(e) => setForm({ ...form, config: e.target.value })} spellCheck="false" /></label>
        <div className="file-row full"><label className="secondary button-file"><FileJson />上传 JSON<input type="file" accept=".json,application/json" onChange={readFile} /></label><button type="button" className="secondary" onClick={test} disabled={testing}>{testing ? <LoaderCircle className="spin" /> : <FlaskConical />}试抓取</button><span>保存前会校验域名、正则与选择器。</span></div>
        {preview && <div className="preview full"><b>{preview.title}</b><span>{preview.published_at || preview.published_text} · 正文 {preview.body_length} 字</span><p>{preview.first_paragraph}</p><a href={preview.url} target="_blank" rel="noreferrer">打开样文<ExternalLink /></a></div>}
        {error && <p className="form-error full">{error}</p>}
      </div>
      <footer className="modal-foot"><button type="button" className="secondary" onClick={onClose}>取消</button><button className="primary" disabled={saving}>{saving && <LoaderCircle className="spin" />}{source ? '保存修改' : '保存来源'}</button></footer>
    </form>
  </Modal>
}

function Metrics({ sources, tasks, records, articles }) {
  const completed = tasks.filter((task) => ['completed', 'completed_with_errors'].includes(task.status)).length
  return <div className="metrics">
    <div className="metric"><span><Database />已接入来源</span><strong>{sources.length}</strong><small>{sources.filter((item) => item.enabled).length} 个已启用</small></div>
    <div className="metric"><span><Activity />持久化任务</span><strong>{tasks.length}</strong><small>{completed} 个已完成</small></div>
    <div className="metric"><span><CheckCircle2 />结构化记录</span><strong>{records.total ?? 0}</strong><small>已由AI总结数量</small></div>
    <div className="metric"><span><CalendarDays />原始数据</span><strong>{articles?.total ?? 0}</strong><small>已采集原文数量</small></div>
  </div>
}

function TaskPanel({ task, onLogs }) {
  if (!task) return <section className="panel task-panel"><div className="panel-head"><h2>最近任务</h2></div><div className="blank compact"><History /><b>尚未创建任务</b><span>选择来源和日期后即可建立任务。</span></div></section>
  return <section className="panel task-panel">
    <div className="panel-head"><div><h2>最近任务</h2><p>任务 #{task.id} · 资讯起始日期 {task.start_date}</p></div><span className={`status ${task.status}`}>{taskStatusLabel(task.status)}</span></div>
    <div className="task-content">
      <div className="task-title">{task.source_snapshot.map((item) => item.name).join('、')}</div>
      <div className="task-time">{formatTime(task.completed_at || task.created_at)}</div>
      <div className="task-stats"><div><span>保存原文</span><b>{task.fetched_count}</b></div><div><span>去重</span><b>{task.deduplicated_count}</b></div><div><span>结构化</span><b>{task.structured_count}</b></div><div><span>失败</span><b>{task.failed_count}</b></div></div>
      <button className="secondary" onClick={() => onLogs(task.id)}>查看运行日志</button>
    </div>
  </section>
}

function DashboardRecordsPreview({ records, onDetail, onHistory, onError }) {
  const items = (records.items || []).slice(0, 5)
  const download = async (format) => {
    try {
      const blob = await api.exportFile(format)
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `半导体资讯结构化结果.${format}`
      document.body.appendChild(link)
      link.click()
      link.remove()
      setTimeout(() => URL.revokeObjectURL(url), 0)
    } catch (err) { onError(err.message) }
  }
  return <section className="panel dashboard-records-preview">
    <div className="panel-head"><div><h2>结构化记录</h2><p>{records.total ?? 0} 条记录，展示最近 {items.length} 条</p></div><div className="preview-actions"><button className="secondary compact-btn" onClick={onHistory}>查看全部</button><button className="export-btn compact-btn" onClick={() => download('xlsx')}><FileSpreadsheet />导出 Excel</button></div></div>
    {items.length > 0 ? <div className="table-wrap"><table><thead><tr><th>地域</th><th>企业名称</th><th>资讯类型</th><th>日期</th><th>信息来源</th><th>操作</th></tr></thead><tbody>{items.map((record) => <tr key={record.id}><td>{record.region || '—'}</td><td className="strong-cell">{record.company_name || record.project_name || '—'}</td><td><span className="type-chip">{record.info_type}</span></td><td>{record.event_date || '—'}</td><td>{record.source_name || '—'}</td><td><button className="text-btn" onClick={() => onDetail(record.id)}><Eye />查看</button></td></tr>)}</tbody></table></div> : <div className="blank compact"><ListFilter /><b>暂无结构化记录</b><span>完成采集并结构化后，记录会显示在这里。</span></div>}
  </section>
}

function Results({ meta, records, filters, setFilters, onDetail, onDelete }) {
  const [exportOpen, setExportOpen] = useState(false)
  const [selected, setSelected] = useState([])
  const items = records.items || []
  const allSelected = items.length > 0 && selected.length === items.length
  const toggle = (id) => setSelected((old) => old.includes(id) ? old.filter((item) => item !== id) : [...old, id])
  const remove = () => { if (selected.length) { onDelete(selected); setSelected([]) } }
  const toggleInfoType = (type) => setFilters({ ...filters, info_type: filters.info_type.includes(type) ? filters.info_type.filter((item) => item !== type) : [...filters.info_type, type] })
  const params = { q: filters.q, region: filters.region, info_type: filters.info_type, source: filters.source, date_from: filters.date_from, date_to: filters.date_to }
  const download = async (format, columns = 'default') => {
    setExportOpen(false)
    const blob = await api.exportFile(format, { ...params, columns })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url; link.download = `半导体资讯结构化结果.${format}`; document.body.appendChild(link); link.click(); link.remove()
    setTimeout(() => URL.revokeObjectURL(url), 0)
  }
  return <section className="panel results-panel">
    <div className="results-head"><div><h2>结构化数据</h2><p>{records.total ?? 0} 条持久化记录</p></div><div className="results-actions">
      {selected.length > 0 && <button className="secondary compact-btn" onClick={remove}><Trash2 />删除 {selected.length} 条</button>}
      <div className="search-field history-search"><Search /><input value={filters.q} onChange={(e) => setFilters({ ...filters, q: e.target.value })} placeholder="全文检索结构化数据" /></div>
      <details className="multi-select"><summary>{filters.info_type.length ? `资讯类型（${filters.info_type.length}）` : '全部资讯类型'}<ChevronDown /></summary><div className="multi-select-menu"><div className="multi-select-head"><b>资讯类型</b>{filters.info_type.length > 0 && <button type="button" onClick={() => setFilters({ ...filters, info_type: [] })}>清除</button>}</div>{meta.info_types?.map((type) => <label key={type}><input type="checkbox" checked={filters.info_type.includes(type)} onChange={() => toggleInfoType(type)} /><span>{type}</span></label>)}</div></details>
      <div className="export-wrap"><button className="export-btn" onClick={() => setExportOpen(!exportOpen)}><Download />导出<ChevronDown /></button>{exportOpen && <div className="export-menu"><button onClick={() => download('xlsx')}><FileSpreadsheet />默认 Excel <small>.xlsx</small></button><button onClick={() => download('csv')}><FileJson />默认 CSV <small>.csv</small></button></div>}</div>
    </div></div>
    <div className="table-wrap"><table><thead><tr><th><input type="checkbox" checked={allSelected} onChange={() => setSelected(allSelected ? [] : items.map((item) => item.id))} /></th><th>地域</th><th>开发区 / 院校</th><th>企业名称</th><th>日期</th><th>资讯类型</th><th>投资金额</th><th>产品 / 项目名称</th><th>信息来源</th><th>原文</th><th>详细信息</th></tr></thead>
      <tbody>{items.map((record) => <tr key={record.id} onDoubleClick={() => onDetail(record.id)}><td><input type="checkbox" checked={selected.includes(record.id)} onChange={() => toggle(record.id)} /></td><td>{record.region || '—'}</td><td>{record.organization || '—'}</td><td className="strong-cell">{record.company_name || '—'}</td><td>{record.event_date || '—'}</td><td><span className="type-chip">{record.info_type}</span></td><td>{record.investment_amount}</td><td>{record.project_name || '—'}</td><td>{record.source_name}</td><td>{record.original_url ? <a className="external" href={record.original_url} target="_blank" rel="noreferrer">打开<ExternalLink /></a> : '—'}</td><td className="detail-cell"><button className="text-btn" onClick={() => onDetail(record.id)}><Eye />查看 / 编辑</button></td></tr>)}</tbody></table></div>
    {!records.items?.length && <div className="blank"><ListFilter /><b>暂无结构化记录</b><span>创建采集任务后，模型会自动生成可审核、可导出的结构化记录。</span></div>}
  </section>
}

function Dashboard({ meta, sources, tasks, records, articles, keywordSetting, onCreateTask, creating, onAddSource, onLogs, onDetail, onHistory, onError }) {
  const [selected, setSelected] = useState([])
  const [startDate, setStartDate] = useState(meta.default_start_date || DEFAULT_DATE)
  const [keywordFilter, setKeywordFilter] = useState(Boolean(keywordSetting.keyword_filter_enabled))
  const [autoStructure, setAutoStructure] = useState(Boolean(keywordSetting.enabled))
  useEffect(() => { setSelected(sources.filter((source) => source.enabled).map((source) => source.id)) }, [sources])
  useEffect(() => { setStartDate(meta.default_start_date || DEFAULT_DATE) }, [meta.default_start_date])
  const toggle = (id) => setSelected((old) => old.includes(id) ? old.filter((item) => item !== id) : [...old, id])
  return <>
    <div className="title-row"><div><span className="eyebrow">本地采集工作台</span><h1>半导体资讯采集</h1><p>选择来源与日期，创建可追溯、可持久化的采集任务。</p></div><button className="primary" onClick={onAddSource}><Plus />添加信息源</button></div>
    <Metrics sources={sources} tasks={tasks} records={records} articles={articles} />
    <div className="workspace-grid"><section className="panel source-panel"><div className="panel-head"><div><h2>创建采集任务</h2><p>来源配置将在创建时生成快照</p></div><span className="selected-count">已选 {selected.length}</span></div>
      <div className="source-list">{sources.map((source) => <label className={`source-row ${!source.enabled ? 'disabled' : ''}`} key={source.id}><input type="checkbox" checked={selected.includes(source.id)} disabled={!source.enabled} onChange={() => toggle(source.id)} /><span className="source-checkmark"><CheckCircle2 /></span><span className="source-copy"><b>{source.name}</b><small>{source.base_url}</small></span><span className={`source-kind ${source.builtin ? '' : 'custom'}`}>{source.builtin ? '内置' : '自定义'}</span></label>)}</div>
      <div className="task-options"><label className="check-field"><input type="checkbox" checked={keywordFilter} onChange={(e) => setKeywordFilter(e.target.checked)} /><span>启用关键词过滤</span></label><label className="check-field"><input type="checkbox" checked={autoStructure} onChange={(e) => setAutoStructure(e.target.checked)} /><span>自动 AI 结构化归档</span></label></div>
      <div className="runbar"><label><CalendarDays /><span>资讯起始日期</span><input aria-label="资讯起始日期" type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} /></label><button className="primary" disabled={!selected.length || creating || (keywordFilter && !keywordSetting.keyword_config?.length)} onClick={() => onCreateTask(selected, startDate, keywordFilter, autoStructure)}>{creating ? <LoaderCircle className="spin" /> : <Activity />}{creating ? '正在创建' : '创建任务'}</button></div>
      {keywordFilter && !keywordSetting.keyword_config?.length && <p className="form-error task-hint">请先在“关键词配置”中添加至少一个关键词。</p>}
    </section><TaskPanel task={tasks[0]} onLogs={onLogs} /></div>
    <DashboardRecordsPreview records={records} onDetail={onDetail} onHistory={onHistory} onError={onError} />
  </>
}

function parseCsv(text) {
  const rows = []; let row = []; let cell = ''; let quoted = false
  for (let i = 0; i < text.length; i += 1) {
    const char = text[i]
    if (char === '"' && quoted && text[i + 1] === '"') { cell += '"'; i += 1 }
    else if (char === '"') quoted = !quoted
    else if (char === ',' && !quoted) { row.push(cell.trim()); cell = '' }
    else if ((char === '\n' || char === '\r') && !quoted) { if (char === '\r' && text[i + 1] === '\n') i += 1; row.push(cell.trim()); if (row.some(Boolean)) rows.push(row); row = []; cell = '' }
    else cell += char
  }
  row.push(cell.trim()); if (row.some(Boolean)) rows.push(row)
  return rows.slice(1).map((values) => ({ industry: values[0] || '', field: values[1] || '', keywords: values.slice(2).join('，') })).filter((item) => Object.values(item).some(Boolean))
}

function KeywordsView({ setting, onSaved }) {
  const [rows, setRows] = useState(setting.keyword_config || [])
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')
  useEffect(() => setRows(setting.keyword_config || []), [setting.keyword_config])
  const update = (index, key, value) => setRows((old) => old.map((item, i) => i === index ? { ...item, [key]: value } : item))
  const upload = (event) => {
    const file = event.target.files?.[0]; if (!file) return
    file.arrayBuffer().then((buffer) => {
      let text = new TextDecoder('utf-8').decode(buffer)
      if (text.includes('\uFFFD')) text = new TextDecoder('gb18030').decode(buffer)
      setRows(parseCsv(text)); setMessage(`已导入 ${parseCsv(text).length} 行，保存后生效`)
    })
  }
  const save = async () => {
    setSaving(true); setMessage('')
    try { const saved = await api.saveModelSetting({ ...setting, api_key: '', keyword_config: rows.filter((row) => Object.values(row).some((v) => v.trim())) }); onSaved(saved); setMessage('关键词配置已保存') }
    catch (err) { setMessage(err.message) } finally { setSaving(false) }
  }
  return <><div className="title-row"><div><span className="eyebrow">采集过滤规则</span><h1>关键词配置</h1><p>按行业、细分领域和产品方向维护关键词，原文标题或正文命中任意层级即进入归档。</p></div><div className="keyword-actions"><label className="secondary button-file"><Upload />导入 CSV<input type="file" accept=".csv,text/csv" onChange={upload} /></label><button className="primary" onClick={save} disabled={saving}><Save />{saving ? '保存中' : '保存配置'}</button></div></div>
    <section className="panel keyword-panel"><div className="table-wrap"><table className="keyword-table"><thead><tr><th>所属行业</th><th>细分领域</th><th>产品及研究方向</th><th aria-label="操作" /></tr></thead><tbody>{rows.map((row, index) => <tr key={index}><td><input value={row.industry || ''} onChange={(e) => update(index, 'industry', e.target.value)} placeholder={index ? '同上可留空' : '例如：新型显示'} /></td><td><input value={row.field || ''} onChange={(e) => update(index, 'field', e.target.value)} placeholder="例如：Micro LED" /></td><td><textarea rows="2" value={row.keywords || ''} onChange={(e) => update(index, 'keywords', e.target.value)} placeholder="多个词用逗号或顿号分隔" /></td><td><button className="icon-btn" aria-label="删除此行" onClick={() => setRows((old) => old.filter((_, i) => i !== index))}><Trash2 /></button></td></tr>)}</tbody></table></div><button className="secondary add-keyword" onClick={() => setRows((old) => [...old, { industry: '', field: '', keywords: '' }])}><Plus />添加一行</button>{message && <p className="keyword-message">{message}</p>}</section></>
}

function AnalyticsGraph({ data, onNodeSelect, fullscreen = false }) {
  const containerRef = useRef(null)
  const graphRef = useRef(null)
  const nodes = data?.graph?.nodes || []
  const edges = data?.graph?.edges || []
  useEffect(() => {
    if (!containerRef.current || !nodes.length) return undefined
    let graph
    let cancelled = false
    import('@antv/g6').then(({ Graph }) => {
      if (cancelled || !containerRef.current) return
      const palette = { 企业: '#1769db', 机构: '#0b9b91', 地域: '#c47718', 项目: '#8a5cc7' }
      const maxValue = Math.max(...nodes.map((node) => node.value || 1), 1)
      graph = new Graph({ container: containerRef.current, autoFit: 'view', data: {
        nodes: nodes.map((node) => ({ id: node.id, data: node, style: { labelText: node.name, labelFill: '#314154', labelFontSize: 11, size: 16 + Math.sqrt((node.value || 1) / maxValue) * 52, fill: palette[node.category] || '#60758b', stroke: '#fff', lineWidth: 2 } })),
        edges: edges.map((edge) => ({ source: edge.source, target: edge.target, style: { lineWidth: Math.min(6, 1 + edge.value), stroke: '#bfd0df' } })),
      }, layout: { type: 'force', preventOverlap: true, nodeSize: 42, linkDistance: 130 }, behaviors: ['drag-canvas', 'zoom-canvas', 'drag-element'] })
      graph.on('node:click', (event) => onNodeSelect(event.target?.id || event.item?.id))
      graph.render()
      graphRef.current = graph
    })
    return () => { cancelled = true; graph?.destroy(); graphRef.current = null }
  }, [nodes, edges, onNodeSelect])
  useEffect(() => {
    const graph = graphRef.current
    if (!graph || !containerRef.current) return
    requestAnimationFrame(() => graph.resize(containerRef.current.clientWidth, containerRef.current.clientHeight))
  }, [fullscreen])
  return nodes.length ? <div ref={containerRef} className="g6-container" aria-label="实体连接网络" /> : <div className="blank"><Network /><b>暂无可连接实体</b><span>完成结构化记录后，这里会生成关系网络。</span></div>
}

function AnalyticsView({ data, loading, filters, setFilters }) {
  const [selectedNode, setSelectedNode] = useState(null)
  const [graphFullscreen, setGraphFullscreen] = useState(false)
  const graphPanelRef = useRef(null)
  const nodes = data?.graph?.nodes || []
  useEffect(() => {
    const handleFullscreen = () => setGraphFullscreen(document.fullscreenElement === graphPanelRef.current)
    document.addEventListener('fullscreenchange', handleFullscreen)
    return () => document.removeEventListener('fullscreenchange', handleFullscreen)
  }, [])
  const toggleGraphFullscreen = async () => {
    if (!document.fullscreenElement) await graphPanelRef.current?.requestFullscreen()
    else await document.exitFullscreen()
  }
  return <>
    <div className="title-row"><div><span className="eyebrow">结构化数据分析</span><h1>关联洞察</h1><p>从结构化记录中发现实体连接、高频词和资讯类型分布。</p></div></div>
    <section className="panel analytics-filters"><div className="search-field"><Search /><input value={filters.q} onChange={(e) => setFilters({ ...filters, q: e.target.value })} placeholder="检索企业、项目或关键词" /></div><input aria-label="开始日期" type="date" value={filters.date_from} onChange={(e) => setFilters({ ...filters, date_from: e.target.value })} /><span>至</span><input aria-label="结束日期" type="date" value={filters.date_to} onChange={(e) => setFilters({ ...filters, date_to: e.target.value })} /><button className="secondary compact-btn" onClick={() => setFilters({ q: '', region: '', info_type: [], source: '', date_from: '', date_to: '' })}>清除筛选</button></section>
    {loading ? <div className="loading"><LoaderCircle className="spin" />正在计算关联洞察</div> : <>
      <div className="analytics-summary"><div><b>{data?.summary?.record_count || 0}</b><span>参与分析记录</span></div><div><b>{data?.summary?.entity_count || 0}</b><span>识别实体</span></div><div><b>{data?.summary?.relation_count || 0}</b><span>连接关系</span></div></div>
      <div className="analytics-grid"><section ref={graphPanelRef} className={`panel analytics-graph ${graphFullscreen ? 'analytics-graph-fullscreen' : ''}`}><div className="panel-head"><div><h2>实体连接网络</h2><p>节点大小表示出现次数，支持拖拽、缩放和画布平移</p></div><button className="icon-btn" onClick={toggleGraphFullscreen} aria-label={graphFullscreen ? '退出全屏' : '全屏查看'} title={graphFullscreen ? '退出全屏' : '全屏查看'}>{graphFullscreen ? <Minimize2 /> : <Maximize2 />}</button></div><AnalyticsGraph data={data} fullscreen={graphFullscreen} onNodeSelect={setSelectedNode} />{selectedNode && <p className="analytics-selection">已选节点：{nodes.find((node) => node.id === selectedNode)?.name || selectedNode}</p>}</section>
        <section className="panel analytics-keywords"><div className="panel-head"><div><h2>高频词</h2><p>按出现次数和涉及记录数排序</p></div></div>{data?.keywords?.length ? <div className="keyword-cloud">{data.keywords.map((item) => <span key={item.text} style={{ fontSize: `${12 + Math.min(13, item.count * 2)}px` }} title={`出现 ${item.count} 次，涉及 ${item.document_count} 条记录`}>{item.text}</span>)}</div> : <div className="blank"><Tags /><b>暂无高频词</b></div>}<div className="analytics-types">{(data?.info_types || []).slice(0, 8).map((item) => <div key={item.name}><span>{item.name}</span><b>{item.value}</b></div>)}</div></section></div>
    </>}
  </>
}

function SourcesView({ sources, onAdd, onEdit, onToggle }) {
  return <><div className="title-row"><div><span className="eyebrow">来源配置</span><h1>信息源管理</h1><p>维护可持久化的 JSON 适配配置与启用状态。</p></div><button className="primary" onClick={onAdd}><Plus />添加信息源</button></div>
    <section className="panel management-list"><div className="list-head"><span>名称</span><span>入口地址</span><span>类型</span><span>状态</span><span>操作</span></div>{sources.map((source) => <div className="management-row" key={source.id}><b>{source.name}</b><a href={source.base_url} target="_blank" rel="noreferrer">{source.base_url}<ExternalLink /></a><span>{source.builtin ? '内置来源' : '自定义'}</span><label className="switch"><input type="checkbox" checked={source.enabled} onChange={(e) => onToggle(source, e.target.checked)} /><i /></label><button className="text-btn" onClick={() => onEdit(source)}>编辑配置</button></div>)}</section>
  </>
}

function RawArticleModal({ article, onClose }) {
  return <Modal title="原始数据详情" onClose={onClose} wide><div className="modal-body raw-detail">
    <div className="raw-detail-head"><div><h3>{article.title}</h3><p>{article.source_name} · {article.published_at || article.published_text || '日期未知'} · 任务 #{article.task_id || '—'}</p></div><a className="external" href={article.canonical_url} target="_blank" rel="noreferrer">打开原文<ExternalLink /></a></div>
    <pre>{article.body}</pre>
  </div></Modal>
}

function RawResults({ articles, query, setQuery, onView, onStructure, structuringIds, onDelete }) {
  const [selected, setSelected] = useState([])
  const items = articles.items || []
  const allSelected = items.length > 0 && selected.length === items.length
  const toggle = (id) => setSelected((old) => old.includes(id) ? old.filter((item) => item !== id) : [...old, id])
  const remove = () => { if (selected.length && window.confirm('删除原始数据将同时删除对应的结构化数据，是否确认删除？')) { onDelete(selected); setSelected([]) } }
  return <section className="panel results-panel">
    <div className="results-head"><div><h2>原始数据</h2><p>{articles.total ?? 0} 篇已存档原文</p></div><div className="results-actions">{selected.length > 0 && <button className="secondary compact-btn" onClick={remove}><Trash2 />删除 {selected.length} 条</button>}<div className="search-field history-search"><Search /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="全文检索标题、正文或网址" /></div></div></div>
    <div className="table-wrap"><table><thead><tr><th><input type="checkbox" checked={allSelected} onChange={() => setSelected(allSelected ? [] : items.map((item) => item.id))} /></th><th>标题</th><th>来源</th><th>发布日期</th><th>采集时间</th><th>状态</th><th>结构化记录</th><th>原文</th><th>操作</th></tr></thead><tbody>{items.map((article) => {
      const canStructure = article.status !== 'completed' && article.record_count === 0
      const isStructuring = structuringIds.has(article.id)
      return <tr key={article.id}><td><input type="checkbox" checked={selected.includes(article.id)} onChange={() => toggle(article.id)} /></td><td className="article-title-cell"><button className="text-btn strong-cell" onClick={() => onView(article)}>{article.title}</button></td><td>{article.source_name}</td><td>{article.published_at || article.published_text || '—'}</td><td>{formatTime(article.collected_at)}</td><td><span className={`status ${article.status}`}>{article.status === 'completed' ? '已结构化' : article.status === 'review_required' ? '处理失败' : '待结构化'}</span></td><td>{article.record_count}</td><td><a className="external" href={article.canonical_url} target="_blank" rel="noreferrer">打开<ExternalLink /></a></td><td>{canStructure ? <button className="primary compact-btn" disabled={isStructuring} onClick={() => onStructure(article.id)}>{isStructuring ? <LoaderCircle className="spin" /> : <FileJson />}{isStructuring ? '处理中' : '结构化'}</button> : <button className="text-btn" onClick={() => onView(article)}><Eye />查看全文</button>}</td></tr>
    })}</tbody></table></div>
    {!articles.items?.length && <div className="blank"><ListFilter /><b>暂无原始数据</b><span>完成采集任务后，原文会保存在这里。</span></div>}
  </section>
}

function HistoryView({ meta, tasks, records, filters, setFilters, articles, articleQuery, setArticleQuery, onLogs, onDetail, onStructure, structuringIds, onDeleteRecords, onDeleteArticles, onDeleteTasks }) {
  const [tab, setTab] = useState('structured')
  const [rawDetail, setRawDetail] = useState(null)
  const [selectedTasks, setSelectedTasks] = useState([])
  const allTasksSelected = tasks.length > 0 && selectedTasks.length === tasks.length
  const removeTasks = () => { if (selectedTasks.length) { onDeleteTasks(selectedTasks); setSelectedTasks([]) } }
  return <><div className="title-row"><div><span className="eyebrow">本地数据档案</span><h1>数据归档</h1><p>分别检索、审核结构化数据与采集原文。</p></div></div>
    <div className="data-tabs" role="tablist"><button className={tab === 'structured' ? 'active' : ''} onClick={() => setTab('structured')}><Database />结构化数据 <span>{records.total ?? 0}</span></button><button className={tab === 'raw' ? 'active' : ''} onClick={() => setTab('raw')}><FileJson />原始数据 <span>{articles.total ?? 0}</span></button><button className={tab === 'tasks' ? 'active' : ''} onClick={() => setTab('tasks')}><History />采集任务 <span>{tasks.length}</span></button></div>
    {tab === 'structured' ? <Results {...{ meta, records, filters, setFilters }} onDetail={onDetail} onDelete={onDeleteRecords} /> : tab === 'raw' ? <RawResults {...{ articles, query: articleQuery, setQuery: setArticleQuery, onStructure, structuringIds }} onView={setRawDetail} onDelete={onDeleteArticles} /> : <section className="panel history-table"><div className="results-head"><div><h2>采集任务</h2><p>{tasks.length} 条任务记录</p></div>{selectedTasks.length > 0 && <button className="secondary compact-btn" onClick={removeTasks}><Trash2 />删除 {selectedTasks.length} 条</button>}</div><table><thead><tr><th><input type="checkbox" checked={allTasksSelected} onChange={() => setSelectedTasks(allTasksSelected ? [] : tasks.map((task) => task.id))} /></th><th>任务</th><th>资讯起始日期</th><th>来源</th><th>状态</th><th>保存原文 / 结构化 / 失败</th><th>创建时间</th><th></th></tr></thead><tbody>{tasks.map((task) => <tr key={task.id}><td><input type="checkbox" checked={selectedTasks.includes(task.id)} onChange={() => setSelectedTasks((old) => old.includes(task.id) ? old.filter((item) => item !== task.id) : [...old, task.id])} /></td><td className="strong-cell">#{task.id}</td><td>{task.start_date}</td><td>{task.source_snapshot.map((item) => item.name).join('、')}</td><td><span className={`status ${task.status}`}>{taskStatusLabel(task.status)}</span></td><td>{task.fetched_count} / {task.structured_count} / {task.failed_count}</td><td>{formatTime(task.created_at)}</td><td><button className="text-btn" onClick={() => onLogs(task.id)}>日志</button></td></tr>)}</tbody></table>{!tasks.length && <div className="blank"><History /><b>暂无任务记录</b></div>}</section>}
    {rawDetail && <RawArticleModal article={rawDetail} onClose={() => setRawDetail(null)} />}
  </>
}

function RecordDetailModal({ id, meta, onClose, onSaved }) {
  const [record, setRecord] = useState(null)
  const [form, setForm] = useState(null)
  const [error, setError] = useState('')
  useEffect(() => { api.record(id).then((data) => { setRecord(data); setForm(data) }).catch((err) => setError(err.message)) }, [id])
  const save = async () => {
    try {
      const fields = ['region', 'organization', 'company_name', 'event_date', 'info_type', 'investment_amount', 'project_name', 'source_name', 'original_url', 'details']
      const saved = await api.updateRecord(id, Object.fromEntries(fields.map((field) => [field, form[field] || (field === 'event_date' ? null : '')])))
      onSaved(saved); onClose()
    } catch (err) { setError(err.message) }
  }
  return <Modal title="结构化记录审核" onClose={onClose} wide><div className="modal-body detail-layout">
    {!form ? <div className="loading"><LoaderCircle className="spin" />正在加载</div> : <><div className="form-grid">
      <label className="field"><span>地域</span><input value={form.region} onChange={(e) => setForm({ ...form, region: e.target.value })} /></label>
      <label className="field"><span>开发区 / 院校</span><input value={form.organization} onChange={(e) => setForm({ ...form, organization: e.target.value })} /></label>
      <label className="field"><span>企业名称</span><input value={form.company_name} onChange={(e) => setForm({ ...form, company_name: e.target.value })} /></label>
      <label className="field"><span>日期</span><input type="date" value={form.event_date || ''} onChange={(e) => setForm({ ...form, event_date: e.target.value })} /></label>
      <label className="field"><span>资讯类型</span><select value={form.info_type} onChange={(e) => setForm({ ...form, info_type: e.target.value })}>{meta.info_types.map((type) => <option key={type}>{type}</option>)}</select></label>
      <label className="field"><span>投资金额</span><input value={form.investment_amount} onChange={(e) => setForm({ ...form, investment_amount: e.target.value })} /></label>
      <label className="field full"><span>产品 / 项目名称</span><input value={form.project_name} onChange={(e) => setForm({ ...form, project_name: e.target.value })} /></label>
      <label className="field full"><span>详细信息</span><textarea rows="5" value={form.details} onChange={(e) => setForm({ ...form, details: e.target.value })} /></label>
    </div><section className="article-review"><h3>{record.article?.title || '原文'}</h3><div className="review-meta"><span className={`status ${record.status}`}>{record.status === 'review_required' ? '待审核' : '已完成'}</span><span>{record.article?.model_name || '—'}</span></div><div className="evidence-list">{Object.entries(record.evidence || {}).map(([field, text]) => <p key={field}><b>{field} · {Math.round((record.confidence?.[field] ?? 0) * 100)}%</b><span>{text}</span></p>)}</div><pre>{record.article?.body}</pre></section></>}
    {error && <p className="form-error">{error}</p>}
  </div>{form && <footer className="modal-foot"><button className="secondary" onClick={onClose}>取消</button><button className="primary" onClick={save}><Save />保存修订</button></footer>}</Modal>
}

function SettingsView() {
  const [form, setForm] = useState({ base_url: 'https://api.deepseek.com', model_name: 'deepseek-v4-flash', api_key: '', enabled: false })
  const [saved, setSaved] = useState(null)
  const [error, setError] = useState('')
  useEffect(() => { api.modelSetting().then((data) => { setSaved(data); setForm((old) => ({ ...old, ...data, api_key: '' })) }).catch((err) => setError(err.message)) }, [])
  const submit = async (event) => { event.preventDefault(); setError(''); try { const result = await api.saveModelSetting(form); setSaved(result); setForm({ ...form, api_key: '' }) } catch (err) { setError(err.message) } }
  return <><div className="title-row"><div><span className="eyebrow">服务端配置</span><h1>API配置</h1><p>配置 OpenAI 兼容模型与自动结构化服务。</p></div></div><div className="settings-grid"><section className="panel model-form"><SlidersHorizontal /><form onSubmit={submit}><h2>结构化模型</h2><label className="field"><span>API 地址</span><input type="url" required value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} /></label><label className="field"><span>模型名称</span><input required value={form.model_name} onChange={(e) => setForm({ ...form, model_name: e.target.value })} /></label><label className="field"><span>API Key {saved?.has_api_key && <small>已保存 {saved.api_key_hint}</small>}</span><input type="password" value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })} placeholder={saved?.has_api_key ? '留空以保留现有密钥' : 'sk-...'} /></label><label className="check-field"><input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} /><span>启用自动结构化（仅任务流程）</span></label><p className="field-note">关闭后，采集任务保留待结构化原文；数据归档仍可使用已配置的 API 手动结构化。</p>{error && <p className="form-error">{error}</p>}<button className="primary"><Save />保存模型设置</button></form></section></div></>
}

export default function App() {
  const [view, setView] = useState('dashboard')
  const [mobileNav, setMobileNav] = useState(false)
  const [meta, setMeta] = useState({ default_start_date: DEFAULT_DATE, info_types: [] })
  const [sources, setSources] = useState([])
  const [tasks, setTasks] = useState([])
  const [records, setRecords] = useState({ items: [], total: 0 })
  const [dashboardRecords, setDashboardRecords] = useState({ items: [], total: 0 })
  const [analytics, setAnalytics] = useState(null)
  const [analyticsLoading, setAnalyticsLoading] = useState(false)
  const [filters, setFilters] = useState({ q: '', region: '', info_type: [], source: '', date_from: '', date_to: '' })
  const [articles, setArticles] = useState({ items: [], total: 0 })
  const [articleQuery, setArticleQuery] = useState('')
  const [structuringIds, setStructuringIds] = useState(() => new Set())
  const [sourceModal, setSourceModal] = useState(null)
  const [logs, setLogs] = useState(null)
  const [detailId, setDetailId] = useState(null)
  const [creating, setCreating] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [keywordSetting, setKeywordSetting] = useState({ keyword_config: [], keyword_filter_enabled: false, enabled: false })

  const load = useCallback(async () => {
    try {
      const [metaData, sourceData, taskData, settingData, recentRecords] = await Promise.all([api.meta(), api.sources(), api.tasks(), api.modelSetting(), api.records({ limit: 5 })])
      setMeta(metaData); setSources(sourceData); setTasks(taskData); setKeywordSetting(settingData); setDashboardRecords(recentRecords)
    } catch (err) { setError(err.message) }
    finally { setLoading(false) }
  }, [])
  const refreshHistory = useCallback(async () => {
    const [recordData, articleData] = await Promise.all([api.records(filters), api.articles({ q: articleQuery })])
    setRecords(recordData); setArticles(articleData)
  }, [filters, articleQuery])
  const refreshAnalytics = useCallback(async () => { setAnalyticsLoading(true); try { setAnalytics(await api.analytics(filters)) } catch (err) { setError(err.message) } finally { setAnalyticsLoading(false) } }, [filters])
  const refreshAll = useCallback(async () => {
    await load()
    await refreshHistory()
    await refreshAnalytics()
  }, [load, refreshHistory, refreshAnalytics])
  useEffect(() => { load() }, [load])
  useEffect(() => { const timer = setInterval(async () => { if (tasks.some((task) => ['queued', 'running'].includes(task.status))) { await load(); await refreshHistory() } }, 2000); return () => clearInterval(timer) }, [tasks, load, refreshHistory])
  useEffect(() => { const timer = setTimeout(() => api.records(filters).then(setRecords).catch((err) => setError(err.message)), 180); return () => clearTimeout(timer) }, [filters])
  useEffect(() => { const timer = setTimeout(refreshAnalytics, 220); return () => clearTimeout(timer) }, [refreshAnalytics])
  useEffect(() => { const timer = setTimeout(() => api.articles({ q: articleQuery }).then(setArticles).catch((err) => setError(err.message)), 180); return () => clearTimeout(timer) }, [articleQuery])

  const createTask = async (sourceIds, startDate, keywordFilter, autoStructure) => {
    setCreating(true); setError('')
    try { const task = await api.createTask({ source_ids: sourceIds, start_date: startDate, keyword_filter_enabled: keywordFilter, auto_structure_enabled: autoStructure, keyword_config: keywordSetting.keyword_config || [] }); setTasks((old) => [task, ...old]) }
    catch (err) { setError(err.message) } finally { setCreating(false) }
  }
  const showLogs = async (id) => { try { setLogs(await api.logs(id)) } catch (err) { setError(err.message) } }
  const sourceSaved = (saved) => setSources((old) => old.some((item) => item.id === saved.id) ? old.map((item) => item.id === saved.id ? saved : item) : [...old, saved])
  const toggleSource = async (source, enabled) => { try { sourceSaved(await api.updateSource(source.id, { enabled })) } catch (err) { setError(err.message) } }
  const structureArticle = async (id) => {
    setStructuringIds((old) => new Set(old).add(id)); setError('')
    try {
      await api.structureArticle(id)
      const [articleData, recordData] = await Promise.all([api.articles({ q: articleQuery }), api.records(filters)])
      setArticles(articleData); setRecords(recordData)
    } catch (err) { setError(err.message) }
    finally { setStructuringIds((old) => { const next = new Set(old); next.delete(id); return next }) }
  }
  const deleteHistory = async (kind, ids) => {
    setError('')
    try {
      if (kind === 'records') await api.deleteRecords(ids)
      else if (kind === 'articles') await api.deleteArticles(ids)
      else await api.deleteTasks(ids)
      await refreshAll()
    } catch (err) { setError(err.message) }
  }
  const activeLabel = useMemo(() => NAV.find((item) => item.id === view)?.label, [view])

  return <div className="app-shell">
    <aside className={mobileNav ? 'open' : ''}><div className="brand"><span>芯</span><div><b>芯闻情报站</b><small>SEMI INTELLIGENCE</small></div></div><nav>{NAV.map(({ id, label, icon: Icon }) => <button key={id} className={view === id ? 'active' : ''} onClick={() => { setView(id); setMobileNav(false) }}><Icon /><span>{label}</span></button>)}</nav><div className="service-state"><i /><div><b>本地服务正常</b></div></div></aside>
    <div className="main-column"><header className="topbar"><button className="mobile-menu icon-btn" onClick={() => setMobileNav(!mobileNav)} aria-label="菜单"><Menu /></button><span>{activeLabel}</span><div><button className="icon-btn" aria-label="刷新" onClick={refreshAll}><RefreshCw /></button></div></header>
      <main>{loading ? <div className="loading"><LoaderCircle className="spin" />正在连接本地服务</div> : view === 'dashboard' ? <Dashboard {...{ meta, sources, tasks, articles, creating, keywordSetting }} records={dashboardRecords} onCreateTask={createTask} onAddSource={() => setSourceModal('new')} onLogs={showLogs} onDetail={(id) => setDetailId(id)} onHistory={() => setView('history')} onError={setError} /> : view === 'sources' ? <SourcesView sources={sources} onAdd={() => setSourceModal('new')} onEdit={setSourceModal} onToggle={toggleSource} /> : view === 'keywords' ? <KeywordsView setting={keywordSetting} onSaved={setKeywordSetting} /> : view === 'history' ? <HistoryView {...{ meta, tasks, records, filters, setFilters, articles, articleQuery, setArticleQuery, structuringIds }} onLogs={showLogs} onDetail={setDetailId} onStructure={structureArticle} onDeleteRecords={(ids) => deleteHistory('records', ids)} onDeleteArticles={(ids) => deleteHistory('articles', ids)} onDeleteTasks={(ids) => deleteHistory('tasks', ids)} /> : view === 'analytics' ? <AnalyticsView data={analytics} loading={analyticsLoading} filters={filters} setFilters={setFilters} /> : <SettingsView />}</main>
    </div>
    {mobileNav && <button className="nav-backdrop" aria-label="关闭菜单" onClick={() => setMobileNav(false)} />}
    {sourceModal && <SourceForm source={sourceModal === 'new' ? null : sourceModal} onClose={() => setSourceModal(null)} onSaved={sourceSaved} />}
    {logs && <Modal title="任务运行日志" onClose={() => setLogs(null)}><div className="log-list">{logs.map((log) => <div key={log.id}><time>{formatTime(log.created_at)}</time><span className={log.level}>{log.level}</span><p>{log.message}</p></div>)}</div></Modal>}
    {detailId && <RecordDetailModal id={detailId} meta={meta} onClose={() => setDetailId(null)} onSaved={(saved) => setRecords((old) => ({ ...old, items: old.items.map((item) => item.id === saved.id ? saved : item) }))} />}
    {error && <div className="toast" role="alert"><span>{error}</span><button className="icon-btn" onClick={() => setError('')}><X /></button></div>}
  </div>
}
