import React, { useCallback, useEffect, useMemo, useState, useRef } from 'react'
import {
  Activity, BookOpen, CalendarDays, CheckCircle2, ChevronDown, ChevronRight,
  Database, Download, ExternalLink, FileJson, FileSpreadsheet, History,
  LayoutDashboard, ListFilter, LoaderCircle, Menu, Plus, RefreshCw, Search,
  Settings, SlidersHorizontal, X, Eye, FlaskConical, Save, Trash2, Tags, Upload,
  Network, Maximize2, Minimize2, Globe2, Rss, Square, Sparkles, Filter, Check,
  Clock, TrendingUp, Layers, Compass, ArrowRight, ShieldCheck, AlertCircle, Info,
  Share2, CheckSquare, BarChart3, Building2, Cpu, MapPin, Briefcase, Play,
  CheckCircle, Copy
} from 'lucide-react'
import { api } from './api'
import * as XLSX from 'xlsx'

const DEFAULT_DATE = '2026-08-01'

const NAV_GROUPS = [
  {
    title: '业务中心',
    items: [
      { id: 'dashboard', label: '采集工作台', icon: LayoutDashboard },
      { id: 'history', label: '数据归档', icon: Database },
      { id: 'analytics', label: '关联洞察', icon: Network },
    ]
  },
  {
    title: '系统配置',
    items: [
      { id: 'keywords', label: '关键词配置', icon: Tags },
      { id: 'sources', label: '信息源管理', icon: BookOpen },
      { id: 'settings', label: 'API配置', icon: Settings },
    ]
  }
]

const ALL_NAV = NAV_GROUPS.flatMap((g) => g.items)

const EMPTY_FORM = {
  mode: 'crawler',
  name: '',
  base_url: '',
  enabled: true,
  search_query: '',
  source_hint: '',
  max_results: 10,
  config: JSON.stringify({
    entry_urls: ['https://example.com/news'],
    article_url_pattern: '/news/',
    selectors: {
      list_links: 'article a',
      title: 'h1',
      date: '.publish-date',
      content: '.content'
    },
    request: {
      rate_limit_per_minute: 20,
      timeout_seconds: 20
    },
  }, null, 2),
}

const DASHSCOPE_BASE_URL = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
const TAVILY_BASE_URL = 'https://api.tavily.com'


function formatTime(value) {
  if (!value) return '—'
  try {
    return new Intl.DateTimeFormat('zh-CN', {
      dateStyle: 'medium',
      timeStyle: 'short',
      timeZone: 'Asia/Shanghai',
    }).format(new Date(value))
  } catch {
    return String(value)
  }
}

const TASK_STATUS_LABELS = {
  queued: '排队中',
  running: '执行中',
  completed: '已完成',
  completed_with_errors: '部分完成',
  failed: '执行失败',
  terminating: '终止中',
  terminated: '已终止',
}

function taskStatusLabel(status) {
  return TASK_STATUS_LABELS[status] || status
}

function Modal({ title, children, onClose, wide = false }) {
  useEffect(() => {
    const handleKeyDown = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [onClose])

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className={`modal ${wide ? 'modal-wide' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="modal-head">
          <h2>{title}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="关闭">
            <X />
          </button>
        </header>
        {children}
      </section>
    </div>
  )
}

function SourceForm({ source, onClose, onSaved }) {
  const [form, setForm] = useState(() => source ? {
    ...source,
    mode: source.source_type || (source.config?.type === 'web_search' ? 'web_search' : 'crawler'),
    search_query: source.config?.query || '',
    source_hint: source.config?.source_hint || '',
    max_results: source.config?.max_results || 10,
    config: JSON.stringify(source.config, null, 2),
  } : EMPTY_FORM)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [preview, setPreview] = useState(null)

  const submit = async (event) => {
    event.preventDefault()
    setError('')
    setSaving(true)
    try {
      const isSearch = form.mode === 'web_search'
      const query = form.search_query.trim()
      const payload = isSearch ? {
        name: `联网搜索：${query.replace(/\s+/g, ' ').slice(0, 42)}`,
        base_url: TAVILY_BASE_URL,
        enabled: form.enabled,
        config: {
          type: 'web_search',
          query,
          source_hint: form.source_hint.trim(),
          max_results: Number(form.max_results),
        },
      } : {
        name: form.name.trim(),
        base_url: form.base_url.trim(),
        enabled: form.enabled,
        config: JSON.parse(form.config)
      }
      const saved = source ? await api.updateSource(source.id, payload) : await api.addSource(payload)
      onSaved(saved)
      onClose()
    } catch (err) {
      setError(err instanceof SyntaxError ? '配置不是有效的 JSON' : err.message)
    } finally {
      setSaving(false)
    }
  }

  const readFile = (event) => {
    const file = event.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => setForm((old) => ({ ...old, config: String(reader.result) }))
    reader.readAsText(file)
  }

  const test = async () => {
    setTesting(true)
    setError('')
    setPreview(null)
    try {
      setPreview(await api.testSource({
        base_url: form.base_url.trim(),
        config: JSON.parse(form.config)
      }))
    } catch (err) {
      setError(err instanceof SyntaxError ? '配置不是有效的 JSON' : err.message)
    } finally {
      setTesting(false)
    }
  }

  return (
    <Modal title={source ? '编辑信息源' : '添加信息源'} onClose={onClose} wide>
      <form className="modal-body" onSubmit={submit}>
        <div className="source-mode">
          <button
            type="button"
            className={form.mode === 'crawler' ? 'active' : ''}
            onClick={() => setForm({ ...form, mode: 'crawler' })}
          >
            <Rss /> 网站爬虫采集
          </button>
          <button
            type="button"
            className={form.mode === 'web_search' ? 'active' : ''}
            onClick={() => setForm({ ...form, mode: 'web_search' })}
          >
            <Globe2 /> Tavily 联网搜索
          </button>
        </div>

        <div className="form-grid">
          <label className="check-field full">
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
            />
            <span>启用此信息源</span>
          </label>

          {form.mode === 'crawler' ? (
            <>
              <label className="field">
                <span>信息源名称</span>
                <input
                  required
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  placeholder="例如：集微网、上海张江官网"
                />
              </label>
              <label className="field">
                <span>站点地址 (Base URL)</span>
                <input
                  required
                  type="url"
                  value={form.base_url}
                  onChange={(e) => setForm({ ...form, base_url: e.target.value })}
                  placeholder="https://example.com"
                />
              </label>
              <label className="field full">
                <span>JSON 来源规则配置</span>
                <textarea
                  rows={12}
                  value={form.config}
                  onChange={(e) => setForm({ ...form, config: e.target.value })}
                  spellCheck="false"
                />
              </label>
              <div className="file-row full">
                <label className="secondary button-file">
                  <FileJson /> 上传 JSON 文件
                  <input type="file" accept=".json,application/json" onChange={readFile} />
                </label>
                <button type="button" className="secondary" onClick={test} disabled={testing}>
                  {testing ? <LoaderCircle className="spin" /> : <FlaskConical />} 试抓取验证
                </button>
                <span className="field-note">保存前系统将校验域名、正则路径与选择器。</span>
              </div>
              {preview && (
                <div className="preview full">
                  <b>{preview.title}</b>
                  <span>{preview.published_at || preview.published_text} · 正文 {preview.body_length} 字</span>
                  <p>{preview.first_paragraph}</p>
                  <a href={preview.url} target="_blank" rel="noreferrer">
                    打开样文链接 <ExternalLink />
                  </a>
                </div>
              )}
            </>
          ) : (
            <div className="search-source-fields full form-grid">
              <label className="field full">
                <span>检索主题与关键意图</span>
                <textarea
                  required
                  rows={4}
                  value={form.search_query}
                  onChange={(e) => setForm({ ...form, search_query: e.target.value })}
                  placeholder="例如：检索中国大陆先进封装、Chiplet 项目的签约、开工和扩产动态，关注投资金额、项目地点与产能。"
                />
              </label>
              <label className="field full">
                <span>网址来源偏好与提示 (可选)</span>
                <textarea
                  rows={3}
                  value={form.source_hint}
                  onChange={(e) => setForm({ ...form, source_hint: e.target.value })}
                  placeholder="例如：优先政府、开发区与企业官网；重点检索 gov.cn、公司新闻中心，也可粘贴具体网址。"
                />
              </label>
              <label className="field">
                <span>检索条数上限</span>
                <input
                  required
                  type="number"
                  min="1"
                  max="20"
                  step="1"
                  value={form.max_results}
                  onChange={(e) => setForm({ ...form, max_results: e.target.value })}
                />
              </label>
              <div className="source-mode-note full">
                <Globe2 />
                <span>任务执行时由 Tavily 搜索、审阅并提取网页正文，经 AI 大模型解析生成可追溯要素记录。</span>
              </div>
            </div>
          )}

          {error && <p className="form-error full"><AlertCircle /> {error}</p>}
        </div>
        <footer className="modal-foot">
          <button type="button" className="secondary" onClick={onClose}>取消</button>
          <button className="primary" disabled={saving}>
            {saving && <LoaderCircle className="spin" />}
            {source ? '保存修改' : '确认添加'}
          </button>
        </footer>
      </form>
    </Modal>
  )
}

function Metrics({ sources, tasks, records, articles }) {
  const completed = tasks.filter((task) => ['completed', 'completed_with_errors'].includes(task.status)).length
  const enabledSources = sources.filter((item) => item.enabled).length

  return (
    <div className="metrics">
      <div className="metric">
        <div className="metric-header">
          <span className="metric-title">已接入来源</span>
          <div className="metric-icon-box">
            <Database />
          </div>
        </div>
        <div className="metric-value-row">
          <strong>{sources.length}</strong>
          <small>{enabledSources} 个已启用</small>
        </div>
      </div>

      <div className="metric">
        <div className="metric-header">
          <span className="metric-title">持久化任务</span>
          <div className="metric-icon-box">
            <Activity />
          </div>
        </div>
        <div className="metric-value-row">
          <strong>{tasks.length}</strong>
          <small>{completed} 个已完成</small>
        </div>
      </div>

      <div className="metric">
        <div className="metric-header">
          <span className="metric-title">结构化记录</span>
          <div className="metric-icon-box">
            <CheckCircle2 />
          </div>
        </div>
        <div className="metric-value-row">
          <strong>{records.total ?? 0}</strong>
          <small>AI 已解析要素条数</small>
        </div>
      </div>

      <div className="metric">
        <div className="metric-header">
          <span className="metric-title">原始数据</span>
          <div className="metric-icon-box">
            <CalendarDays />
          </div>
        </div>
        <div className="metric-value-row">
          <strong>{articles?.total ?? 0}</strong>
          <small>已归档原文快照</small>
        </div>
      </div>
    </div>
  )
}

function TaskPanel({ task, onLogs, onTerminate, stoppingIds }) {
  if (!task) {
    return (
      <section className="panel task-panel">
        <div className="panel-head">
          <div>
            <h2>最近任务</h2>
            <p>实时监控采集进程与执行指标</p>
          </div>
        </div>
        <div className="blank compact">
          <History />
          <b>尚未创建任务</b>
          <span>选择来源和起始日期后即可建立采集任务。</span>
        </div>
      </section>
    )
  }

  const isRunning = ['queued', 'running'].includes(task.status)
  const isStopping = stoppingIds.has(task.id)

  return (
    <section className="panel task-panel">
      <div className="panel-head">
        <div>
          <h2>最近任务</h2>
          <p>任务 #{task.id} · 资讯起始日期 {task.start_date}</p>
        </div>
        <span className={`status ${task.status}`}>
          {taskStatusLabel(task.status)}
        </span>
      </div>
      <div className="task-content">
        <div className="task-title">
          {task.source_snapshot.map((item) => item.name).join('、')}
        </div>
        <div className="task-time">
          <Clock />
          <span>{formatTime(task.completed_at || task.created_at)}</span>
        </div>
        <div className="task-stats">
          <div className="task-stat-item">
            <span>保存原文</span>
            <b>{task.fetched_count}</b>
          </div>
          <div className="task-stat-item">
            <span>智能去重</span>
            <b>{task.deduplicated_count}</b>
          </div>
          <div className="task-stat-item">
            <span>AI 结构化</span>
            <b>{task.structured_count}</b>
          </div>
          <div className={`task-stat-item ${task.failed_count > 0 ? 'is-alert' : ''}`}>
            <span>异常失败</span>
            <b>{task.failed_count}</b>
          </div>
        </div>
        <div className="task-actions">
          <button className="secondary" onClick={() => onLogs(task.id)}>
            <History /> 查看运行日志
          </button>
          {isRunning && (
            <button
              className="danger"
              disabled={isStopping}
              onClick={() => onTerminate(task.id)}
            >
              {isStopping ? <LoaderCircle className="spin" /> : <Square />}
              {isStopping ? '终止中' : '终止任务'}
            </button>
          )}
        </div>
      </div>
    </section>
  )
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
    } catch (err) {
      onError(err.message)
    }
  }

  return (
    <section className="panel dashboard-records-preview">
      <div className="panel-head">
        <div>
          <h2>最新结构化记录</h2>
          <p>共 {records.total ?? 0} 条记录，当前展示最新 {items.length} 条</p>
        </div>
        <div className="preview-actions">
          <button className="secondary compact-btn" onClick={onHistory}>
            查看全部数据 <ArrowRight />
          </button>
          <button className="export-btn compact-btn" onClick={() => download('xlsx')}>
            <FileSpreadsheet /> 导出 Excel
          </button>
        </div>
      </div>
      {items.length > 0 ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>地域</th>
                <th>企业 / 机构名称</th>
                <th>资讯类型</th>
                <th>投资金额</th>
                <th className="date-cell">事件日期</th>
                <th>信息来源</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {items.map((record) => (
                <tr key={record.id}>
                  <td>
                    {record.region ? (
                      <span className="region-chip">{record.region}</span>
                    ) : '—'}
                  </td>
                  <td className="strong-cell">
                    {record.company_name || record.organization || record.project_name || '—'}
                  </td>
                  <td>
                    <span className="type-chip" data-type={record.info_type || ''}>
                      {record.info_type || '其他'}
                    </span>
                  </td>
                  <td>
                    {record.investment_amount && record.investment_amount !== '—' ? (
                      <span className="money-highlight">{record.investment_amount}</span>
                    ) : '—'}
                  </td>
                  <td className="date-cell">{record.event_date || '—'}</td>
                  <td>{record.source_name || '—'}</td>
                  <td>
                    <button className="text-btn" onClick={() => onDetail(record.id)}>
                      <Eye /> 查看详情
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="blank compact">
          <ListFilter />
          <b>暂无结构化记录</b>
          <span>完成采集并由 AI 解析后，结构化要素记录将展示在这里。</span>
        </div>
      )}
    </section>
  )
}

function Results({ meta, records, filters, setFilters, onDetail, onDelete }) {
  const [exportOpen, setExportOpen] = useState(false)
  const [selected, setSelected] = useState([])
  const items = records.items || []
  const allSelected = items.length > 0 && selected.length === items.length

  const toggle = (id) => {
    setSelected((old) => old.includes(id) ? old.filter((item) => item !== id) : [...old, id])
  }

  const remove = () => {
    if (selected.length && window.confirm(`确定删除选中的 ${selected.length} 条结构化记录吗？`)) {
      onDelete(selected)
      setSelected([])
    }
  }

  const toggleInfoType = (type) => {
    setFilters({
      ...filters,
      info_type: filters.info_type.includes(type)
        ? filters.info_type.filter((item) => item !== type)
        : [...filters.info_type, type]
    })
  }

  const params = {
    q: filters.q,
    region: filters.region,
    info_type: filters.info_type,
    source: filters.source,
    date_from: filters.date_from,
    date_to: filters.date_to
  }

  const download = async (format, columns = 'default') => {
    setExportOpen(false)
    const blob = await api.exportFile(format, { ...params, columns })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `半导体资讯结构化结果.${format}`
    document.body.appendChild(link)
    link.click()
    link.remove()
    setTimeout(() => URL.revokeObjectURL(url), 0)
  }

  return (
    <section className="panel results-panel">
      <div className="results-head">
        <div>
          <h2>结构化数据</h2>
          <p>共 {records.total ?? 0} 条要素记录</p>
        </div>
        <div className="results-actions">
          {selected.length > 0 && (
            <button className="secondary compact-btn" onClick={remove}>
              <Trash2 /> 删除 {selected.length} 条
            </button>
          )}
          <div className="search-field history-search">
            <Search />
            <input
              value={filters.q}
              onChange={(e) => setFilters({ ...filters, q: e.target.value })}
              placeholder="全文检索企业、项目、地域或关键词"
            />
            {filters.q && (
              <button
                type="button"
                className="icon-btn"
                style={{ width: 22, height: 22, minWidth: 22 }}
                onClick={() => setFilters({ ...filters, q: '' })}
              >
                <X style={{ width: 14, height: 14 }} />
              </button>
            )}
          </div>
          <details className="multi-select">
            <summary>
              <Filter />
              {filters.info_type.length ? `资讯类型 (${filters.info_type.length})` : '全部资讯类型'}
              <ChevronDown />
            </summary>
            <div className="multi-select-menu">
              <div className="multi-select-head">
                <b>资讯类型筛选</b>
                {filters.info_type.length > 0 && (
                  <button type="button" onClick={() => setFilters({ ...filters, info_type: [] })}>
                    清除已选
                  </button>
                )}
              </div>
              {meta.info_types?.map((type) => (
                <label key={type}>
                  <input
                    type="checkbox"
                    checked={filters.info_type.includes(type)}
                    onChange={() => toggleInfoType(type)}
                  />
                  <span>{type}</span>
                </label>
              ))}
            </div>
          </details>
          <div className="export-wrap">
            <button className="export-btn" onClick={() => setExportOpen(!exportOpen)}>
              <Download /> 导出数据 <ChevronDown />
            </button>
            {exportOpen && (
              <div className="export-menu">
                <button onClick={() => download('xlsx')}>
                  <FileSpreadsheet /> 导出为 Excel <small>.xlsx</small>
                </button>
                <button onClick={() => download('csv')}>
                  <FileJson /> 导出为 CSV <small>.csv</small>
                </button>
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th style={{ width: 44 }}>
                <input
                  type="checkbox"
                  checked={allSelected}
                  onChange={() => setSelected(allSelected ? [] : items.map((item) => item.id))}
                />
              </th>
              <th>地域</th>
              <th>开发区 / 院校</th>
              <th>企业名称</th>
              <th>资讯类型</th>
              <th>投资金额</th>
              <th>产品 / 项目名称</th>
              <th className="date-cell">事件日期</th>
              <th>信息来源</th>
              <th>原文</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {items.map((record) => (
              <tr key={record.id} onDoubleClick={() => onDetail(record.id)}>
                <td>
                  <input
                    type="checkbox"
                    checked={selected.includes(record.id)}
                    onChange={() => toggle(record.id)}
                  />
                </td>
                <td>
                  {record.region ? (
                    <span className="region-chip">{record.region}</span>
                  ) : '—'}
                </td>
                <td>{record.organization || '—'}</td>
                <td className="strong-cell">{record.company_name || '—'}</td>
                <td>
                  <span className="type-chip" data-type={record.info_type || ''}>
                    {record.info_type || '其他'}
                  </span>
                </td>
                <td>
                  {record.investment_amount && record.investment_amount !== '—' ? (
                    <span className="money-highlight">{record.investment_amount}</span>
                  ) : '—'}
                </td>
                <td>{record.project_name || '—'}</td>
                <td className="date-cell">{record.event_date || '—'}</td>
                <td>{record.source_name || '—'}</td>
                <td>
                  {record.original_url ? (
                    <a className="external" href={record.original_url} target="_blank" rel="noreferrer">
                      链接 <ExternalLink />
                    </a>
                  ) : '—'}
                </td>
                <td className="detail-cell">
                  <button className="text-btn" onClick={() => onDetail(record.id)}>
                    <Eye /> 审核 / 编辑
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!records.items?.length && (
        <div className="blank">
          <ListFilter />
          <b>暂无结构化记录</b>
          <span>创建采集任务后，大模型将自动提取并生成可审核、可导出的要素记录。</span>
        </div>
      )}
    </section>
  )
}

function Dashboard({
  meta, sources, tasks, records, articles, keywordSetting,
  onCreateTask, creating, onAddSource, onLogs, onTerminate,
  stoppingIds, onDetail, onHistory, onError
}) {
  const [selected, setSelected] = useState([])
  const sourceSelectionInitialized = useRef(false)
  const previousEnabledSourceIds = useRef(new Set())
  const [startDate, setStartDate] = useState(meta.default_start_date || DEFAULT_DATE)
  const [keywordFilter, setKeywordFilter] = useState(true)
  const [autoStructure, setAutoStructure] = useState(true)
  const [sourceSearch, setSourceSearch] = useState('')

  useEffect(() => {
    const enabledIds = sources.filter((source) => source.enabled).map((source) => source.id)
    const enabledSet = new Set(enabledIds)
    if (!sourceSelectionInitialized.current) {
      sourceSelectionInitialized.current = true
      previousEnabledSourceIds.current = enabledSet
      setSelected(enabledIds)
      return
    }
    const newlyEnabled = enabledIds.filter((id) => !previousEnabledSourceIds.current.has(id))
    previousEnabledSourceIds.current = enabledSet
    setSelected((old) => {
      const next = [...old.filter((id) => enabledSet.has(id)), ...newlyEnabled.filter((id) => !old.includes(id))]
      return next.length === old.length && next.every((id, index) => id === old[index]) ? old : next
    })
  }, [sources])

  useEffect(() => {
    setStartDate(meta.default_start_date || DEFAULT_DATE)
  }, [meta.default_start_date])

  const latestTask = tasks[0]

  const filteredSources = useMemo(() => {
    if (!sourceSearch.trim()) return sources
    const q = sourceSearch.toLowerCase().trim()
    return sources.filter((s) =>
      s.name.toLowerCase().includes(q) ||
      (s.config?.query && s.config.query.toLowerCase().includes(q)) ||
      (s.base_url && s.base_url.toLowerCase().includes(q))
    )
  }, [sources, sourceSearch])

  const selectAll = () => setSelected(sources.map((s) => s.id))
  const selectNone = () => setSelected([])
  const selectWebSearch = () => setSelected(sources.filter((s) => s.source_type === 'web_search').map((s) => s.id))
  const selectCrawlers = () => setSelected(sources.filter((s) => s.source_type !== 'web_search').map((s) => s.id))

  const handleCreateTask = () => {
    if (selected.length === 0) {
      onError('请至少选择一个信息源')
      return
    }
    onCreateTask({
      source_ids: selected,
      start_date: startDate,
      keyword_filter_enabled: keywordFilter,
      auto_structure_enabled: autoStructure,
    })
  }

  return (
    <>
      <div className="title-row">
        <div className="title-content">
          <span className="eyebrow">情报采集引擎</span>
          <h1>采集工作台</h1>
          <p>调度多源爬虫与 Tavily 联网检索，实时沉淀半导体产业高价值情报。</p>
        </div>
        <div className="title-actions">
          <button className="secondary" onClick={onAddSource}>
            <Plus /> 添加信息源
          </button>
        </div>
      </div>

      <Metrics sources={sources} tasks={tasks} records={records} articles={articles} />

      <div className="workspace-grid">
        <section className="panel source-panel">
          <div className="panel-head">
            <div>
              <h2>信息源选择</h2>
              <p>已选择 {selected.length} / {sources.length} 个信息源</p>
            </div>
          </div>

          <div className="source-filter-bar">
            <div className="source-search-input">
              <Search />
              <input
                value={sourceSearch}
                onChange={(e) => setSourceSearch(e.target.value)}
                placeholder="筛选信息源或主题..."
              />
              {sourceSearch && (
                <button
                  type="button"
                  className="icon-btn"
                  style={{ width: 20, height: 20 }}
                  onClick={() => setSourceSearch('')}
                >
                  <X style={{ width: 12, height: 12 }} />
                </button>
              )}
            </div>
            <div className="source-quick-actions">
              <button type="button" onClick={selectAll}>全选</button>
              <button type="button" onClick={selectNone}>清空</button>
              <button type="button" onClick={selectWebSearch}>仅联网</button>
              <button type="button" onClick={selectCrawlers}>仅爬虫</button>
            </div>
          </div>

          <div className="source-list">
            {filteredSources.map((source) => {
              const isChecked = selected.includes(source.id)
              const isWebSearch = source.source_type === 'web_search'
              return (
                <label
                  key={source.id}
                  className={`source-row ${!source.enabled ? 'disabled' : ''}`}
                >
                  <input
                    type="checkbox"
                    checked={isChecked}
                    onChange={(e) => {
                      if (e.target.checked) setSelected([...selected, source.id])
                      else setSelected(selected.filter((id) => id !== source.id))
                    }}
                  />
                  <span className="source-checkmark">
                    {isChecked && <Check />}
                  </span>
                  <div className="source-copy">
                    <b>{source.name}</b>
                    <small>
                      {isWebSearch ? source.config?.query : source.base_url}
                    </small>
                  </div>
                  <span className={`source-kind ${isWebSearch ? 'web-search' : 'custom'}`}>
                    {isWebSearch ? '🌐 联网' : '🕷️ 爬虫'}
                  </span>
                </label>
              )
            })}
            {!filteredSources.length && (
              <div className="blank compact">
                <Search />
                <b>无匹配信息源</b>
                <span>未找到与“{sourceSearch}”相匹配的信息源。</span>
              </div>
            )}
          </div>
        </section>

        <div className="dashboard-right-column">
          <section className="panel run-panel">
            <div className="panel-head">
              <div>
                <h2>启动配置</h2>
                <p>设置起始日期与智能过滤策略</p>
              </div>
            </div>

            <div className="run-body">
              <label className="run-field">
                <span><CalendarDays /> 资讯起始日期</span>
                <input
                  type="date"
                  value={startDate}
                  onChange={(e) => setStartDate(e.target.value)}
                />
              </label>

              <div className="run-toggles">
                <label className="toggle-card">
                  <input
                    type="checkbox"
                    checked={keywordFilter}
                    onChange={(e) => setKeywordFilter(e.target.checked)}
                  />
                  <div className="toggle-card-copy">
                    <b>三维关键词过滤</b>
                    <small>技术词 · 名词 · 动词需同时命中</small>
                  </div>
                </label>
                <label className="toggle-card">
                  <input
                    type="checkbox"
                    checked={autoStructure}
                    onChange={(e) => setAutoStructure(e.target.checked)}
                  />
                  <div className="toggle-card-copy">
                    <b>AI 自动结构化</b>
                    <small>入库时同步解析要素字段</small>
                  </div>
                </label>
              </div>

              <button
                className="primary run-btn"
                disabled={creating || selected.length === 0}
                onClick={handleCreateTask}
              >
                {creating ? <LoaderCircle className="spin" /> : <Play />}
                {creating ? '正在创建任务...' : `建立采集任务${selected.length ? ` · ${selected.length} 个来源` : ''}`}
              </button>
            </div>
          </section>

          <TaskPanel
            task={latestTask}
            onLogs={onLogs}
            onTerminate={onTerminate}
            stoppingIds={stoppingIds}
          />
        </div>
      </div>

      <DashboardRecordsPreview
        records={records}
        onDetail={onDetail}
        onHistory={onHistory}
        onError={onError}
      />
    </>
  )
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
      const palette = {
        企业: '#2563eb',
        机构: '#0d9488',
        地域: '#d97706',
        项目: '#7c3aed',
      }
      const maxValue = Math.max(...nodes.map((node) => node.value || 1), 1)

      graph = new Graph({
        container: containerRef.current,
        autoFit: 'view',
        data: {
          nodes: nodes.map((node) => ({
            id: node.id,
            data: node,
            style: {
              labelText: node.name,
              labelFill: '#1e293b',
              labelFontSize: 11,
              labelFontWeight: 500,
              size: 18 + Math.sqrt((node.value || 1) / maxValue) * 50,
              fill: palette[node.category] || '#64748b',
              stroke: '#ffffff',
              lineWidth: 2.5,
              shadowColor: 'rgba(0, 0, 0, 0.1)',
              shadowBlur: 8,
            }
          })),
          edges: edges.map((edge) => ({
            source: edge.source,
            target: edge.target,
            style: {
              lineWidth: Math.min(5, 1.2 + edge.value * 0.8),
              stroke: '#cbd5e1',
            }
          })),
        },
        layout: {
          type: 'force',
          preventOverlap: true,
          nodeSize: 46,
          linkDistance: 130,
        },
        behaviors: ['drag-canvas', 'zoom-canvas', 'drag-element']
      })

      graph.on('node:click', (event) => onNodeSelect(event.target?.id || event.item?.id))
      graph.render()
      graphRef.current = graph
    })

    return () => {
      cancelled = true
      graph?.destroy()
      graphRef.current = null
    }
  }, [nodes, edges, onNodeSelect])

  useEffect(() => {
    const graph = graphRef.current
    if (!graph || !containerRef.current) return
    requestAnimationFrame(() => graph.resize(containerRef.current.clientWidth, containerRef.current.clientHeight))
  }, [fullscreen])

  return nodes.length ? (
    <div ref={containerRef} className="g6-container" aria-label="实体连接网络" />
  ) : (
    <div className="blank">
      <Network />
      <b>暂无可连接实体</b>
      <span>完成结构化记录解析后，系统将自动计算生成实体关系知识图谱。</span>
    </div>
  )
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

  return (
    <>
      <div className="title-row">
        <div className="title-content">
          <span className="eyebrow">结构化数据分析</span>
          <h1>关联洞察</h1>
          <p>多维透视半导体产业核心实体关系、热点关键词与资讯类型分布态势。</p>
        </div>
      </div>

      <section className="panel analytics-filters">
        <div className="search-field">
          <Search />
          <input
            value={filters.q}
            onChange={(e) => setFilters({ ...filters, q: e.target.value })}
            placeholder="检索企业、项目或关键词"
          />
        </div>
        <div className="analytics-filter-dates">
          <input
            aria-label="开始日期"
            type="date"
            value={filters.date_from}
            onChange={(e) => setFilters({ ...filters, date_from: e.target.value })}
          />
          <span>至</span>
          <input
            aria-label="结束日期"
            type="date"
            value={filters.date_to}
            onChange={(e) => setFilters({ ...filters, date_to: e.target.value })}
          />
        </div>
        <button
          className="secondary compact-btn"
          onClick={() => setFilters({ q: '', region: '', info_type: [], source: '', date_from: '', date_to: '' })}
        >
          清除筛选
        </button>
      </section>

      {loading ? (
        <div className="loading">
          <LoaderCircle className="spin" />
          <span>正在计算实体关联洞察图谱...</span>
        </div>
      ) : (
        <>
          <div className="analytics-summary">
            <div>
              <b>{data?.summary?.record_count || 0}</b>
              <span>参与分析记录</span>
            </div>
            <div>
              <b>{data?.summary?.entity_count || 0}</b>
              <span>识别实体节点</span>
            </div>
            <div>
              <b>{data?.summary?.relation_count || 0}</b>
              <span>连接拓扑关系</span>
            </div>
          </div>

          <div className="analytics-grid">
            <section
              ref={graphPanelRef}
              className={`panel analytics-graph ${graphFullscreen ? 'analytics-graph-fullscreen' : ''}`}
            >
              <div className="panel-head">
                <div>
                  <h2>实体连接拓扑网络</h2>
                  <p>节点大小表示出现频次，支持滚轮缩放、画布平移与节点拖拽探查</p>
                </div>
                <div className="graph-head-actions">
                  <button
                    className="icon-btn"
                    onClick={toggleGraphFullscreen}
                    aria-label={graphFullscreen ? '退出全屏' : '全屏查看'}
                    title={graphFullscreen ? '退出全屏' : '全屏查看'}
                  >
                    {graphFullscreen ? <Minimize2 /> : <Maximize2 />}
                  </button>
                </div>
              </div>
              <AnalyticsGraph
                data={data}
                fullscreen={graphFullscreen}
                onNodeSelect={setSelectedNode}
              />
              {selectedNode && (
                <p className="analytics-selection">
                  <Info style={{ width: 14, height: 14 }} />
                  <span>已选中节点：<b>{nodes.find((node) => node.id === selectedNode)?.name || selectedNode}</b></span>
                </p>
              )}
            </section>

            <section className="panel analytics-keywords">
              <div className="panel-head">
                <div>
                  <h2>产业热点高频词</h2>
                  <p>按词频权重与涉及资讯记录数加权展示</p>
                </div>
              </div>
              {data?.keywords?.length ? (
                <div className="keyword-cloud">
                  {data.keywords.map((item) => (
                    <span
                      key={item.text}
                      style={{ fontSize: `${12 + Math.min(12, item.count * 2)}px` }}
                      title={`出现 ${item.count} 次，涉及 ${item.document_count} 条记录`}
                    >
                      {item.text}
                    </span>
                  ))}
                </div>
              ) : (
                <div className="blank">
                  <Tags />
                  <b>暂无高频词</b>
                </div>
              )}

              <div className="analytics-types-header">
                <b>资讯类型占比</b>
              </div>
              <div className="analytics-types">
                {(data?.info_types || []).slice(0, 8).map((item) => {
                  const maxValue = Math.max(...(data.info_types || []).map((t) => t.value), 1)
                  const pct = Math.round((item.value / maxValue) * 100)
                  return (
                    <div className="type-row" key={item.name}>
                      <span className="type-row-name">{item.name}</span>
                      <div className="type-row-bar"><i style={{ width: `${Math.max(pct, 4)}%` }} /></div>
                      <b>{item.value}</b>
                    </div>
                  )
                })}
              </div>
            </section>
          </div>
        </>
      )}
    </>
  )
}

function isHeaderRow(row, rows) {
  if (!row || rows.length < 2) return false
  const cells = row.map((v) => (v == null ? '' : String(v).trim())).filter(Boolean)
  if (!cells.length) return false
  return cells.every((c) => c.length <= 12 && !/[、，,]/.test(c))
}

function technicalSheetRows(sheet) {
  if (!sheet) return []
  const rows = XLSX.utils.sheet_to_json(sheet, { header: 1 })
  const start = isHeaderRow(rows[0], rows) ? 1 : 0
  return rows.slice(start).map((values) => ({
    category: values[0] || '',
    field: values[1] || '',
    keywords: values.slice(2).filter((v) => v != null && String(v).trim()).join('，')
  })).filter((item) => Object.values(item).some(Boolean))
}

function sheetRows(sheet) {
  if (!sheet) return []
  const rows = XLSX.utils.sheet_to_json(sheet, { header: 1 })
  const start = isHeaderRow(rows[0], rows) ? 1 : 0
  return rows.slice(start).map((values) => {
    const cells = (values || []).map((v) => (v == null ? '' : String(v).trim()))
    const nonEmpty = cells.filter(Boolean)
    if (!nonEmpty.length) return null
    // 单列布局：整格直接作为关键词（Sheet2/Sheet3 常见，如“一期、二期、…、产业园”）
    if (nonEmpty.length === 1) {
      return { industry: '', field: '', keywords: cells[0] }
    }
    return { industry: cells[0], field: cells[1], keywords: cells.slice(2).join('，') }
  }).filter(Boolean)
}

function KeywordsView({ setting, onSaved }) {
  const initial = setting.keyword_config && !Array.isArray(setting.keyword_config)
    ? setting.keyword_config
    : { technical: setting.keyword_config || [], industry_noun: [], industry_verb: [] }
  const [groups, setGroups] = useState(initial)
  const [tab, setTab] = useState('technical')
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')

  useEffect(() => {
    const next = setting.keyword_config && !Array.isArray(setting.keyword_config)
      ? setting.keyword_config
      : { technical: setting.keyword_config || [], industry_noun: [], industry_verb: [] }
    setGroups(next)
  }, [setting.keyword_config])

  const rows = groups[tab] || []

  const update = (index, key, value) => {
    setGroups((old) => ({
      ...old,
      [tab]: old[tab].map((item, i) => (i === index ? { ...item, [key]: value } : item))
    }))
  }

  const addRow = () => {
    const newRow = tab === 'technical'
      ? { category: '', field: '', keywords: '' }
      : { industry: '', field: '', keywords: '' }
    setGroups((old) => ({
      ...old,
      [tab]: [...(old[tab] || []), newRow]
    }))
  }

  const removeRow = (index) => {
    setGroups((old) => ({
      ...old,
      [tab]: old[tab].filter((_, i) => i !== index)
    }))
  }

  const upload = (event) => {
    const file = event.target.files?.[0]
    if (!file) return
    if (!file.name.toLowerCase().endsWith('.xlsx')) {
      setMessage('导入失败：仅支持 .xlsx 格式工作簿')
      event.target.value = ''
      return
    }
    file.arrayBuffer().then((buffer) => {
      const workbook = XLSX.read(buffer, { type: 'array' })
      const names = workbook.SheetNames
      if (names.length < 3) {
        setMessage('导入失败：Excel 文件必须包含 Sheet1 (技术词)、Sheet2 (项目名词)、Sheet3 (项目动词)')
        return
      }
      const next = {
        technical: technicalSheetRows(workbook.Sheets[names[0]]),
        industry_noun: sheetRows(workbook.Sheets[names[1]]),
        industry_verb: sheetRows(workbook.Sheets[names[2]]),
      }
      setGroups(next)
      setMessage('已导入关键词工作簿，点击“保存配置”后生效')
    })
  }

  const save = async () => {
    setSaving(true)
    setMessage('')
    try {
      const cleaned = Object.fromEntries(
        Object.entries(groups).map(([key, values]) => [
          key,
          values.filter((row) => Object.values(row).some((v) => String(v).trim()))
        ])
      )
      const saved = await api.saveModelSetting({
        ...setting,
        api_key: '',
        keyword_config: cleaned,
      })
      onSaved(saved)
      setMessage('关键词配置已成功保存')
    } catch (err) {
      setMessage(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      <div className="title-row">
        <div className="title-content">
          <span className="eyebrow">采集过滤规则</span>
          <h1>关键词配置</h1>
          <p>文章需同时命中技术名词、项目名词和项目动词，才会通过相关性校验进入归档。</p>
        </div>
        <div className="keyword-actions">
          <label className="secondary button-file">
            <Upload /> 导入 Excel 词表
            <input
              type="file"
              accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              onChange={upload}
            />
          </label>
          <button className="primary" onClick={save} disabled={saving}>
            <Save /> {saving ? '保存中...' : '保存配置'}
          </button>
        </div>
      </div>

      {message && (
        <div className="keyword-feedback">
          <Info />
          <span>{message}</span>
        </div>
      )}

      <div className="data-tabs" role="tablist">
        <button
          className={tab === 'technical' ? 'active' : ''}
          onClick={() => setTab('technical')}
        >
          <Cpu /> 核心技术词 (Sheet1) <span>{groups.technical?.length || 0}</span>
        </button>
        <button
          className={tab === 'industry_noun' ? 'active' : ''}
          onClick={() => setTab('industry_noun')}
        >
          <Building2 /> 项目名词 (Sheet2) <span>{groups.industry_noun?.length || 0}</span>
        </button>
        <button
          className={tab === 'industry_verb' ? 'active' : ''}
          onClick={() => setTab('industry_verb')}
        >
          <TrendingUp /> 项目动词 (Sheet3) <span>{groups.industry_verb?.length || 0}</span>
        </button>
      </div>

      <section className="panel keyword-table-panel">
        <div className="panel-head">
          <div>
            <h2>
              {tab === 'technical' ? '技术词汇表' : tab === 'industry_noun' ? '项目名词表' : '项目动词表'}
            </h2>
            <p>多个近义词请用中文逗号（，）或顿号分隔</p>
          </div>
          <button className="secondary compact-btn" onClick={addRow}>
            <Plus /> 添加词条行
          </button>
        </div>

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th style={{ width: '22%' }}>{tab === 'technical' ? '类别' : '行业/分类'}</th>
                <th style={{ width: '22%' }}>细分领域</th>
                <th>关联关键词 (逗号分隔)</th>
                <th style={{ width: 60 }}>操作</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => {
                const firstKey = tab === 'technical' ? 'category' : 'industry'
                return (
                  <tr key={index}>
                    <td>
                      <input
                        className="cell-input"
                        value={row[firstKey] || ''}
                        onChange={(e) => update(index, firstKey, e.target.value)}
                        placeholder="例如：封测 / EDA"
                      />
                    </td>
                    <td>
                      <input
                        className="cell-input"
                        value={row.field || ''}
                        onChange={(e) => update(index, 'field', e.target.value)}
                        placeholder="例如：先进封装"
                      />
                    </td>
                    <td>
                      <input
                        className="cell-input"
                        value={row.keywords || ''}
                        onChange={(e) => update(index, 'keywords', e.target.value)}
                        placeholder="例如：2.5D, 3D IC, Chiplet, CoWoS"
                      />
                    </td>
                    <td>
                      <button
                        className="icon-btn text-btn danger-text"
                        type="button"
                        onClick={() => removeRow(index)}
                        title="删除该行"
                      >
                        <Trash2 />
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        {!rows.length && (
          <div className="blank compact">
            <Tags />
            <b>该分类下暂无词条</b>
            <span>点击上方“添加词条行”或导入 Excel 模板初始化词库。</span>
          </div>
        )}
      </section>
    </>
  )
}

function SourcesView({ sources, onAdd, onEdit, onToggle, onDelete, deletingIds }) {
  return (
    <>
      <div className="title-row">
        <div className="title-content">
          <span className="eyebrow">来源配置</span>
          <h1>信息源管理</h1>
          <p>统一维护传统网站爬虫节点与 Tavily 联网检索任务意图。</p>
        </div>
        <button className="primary" onClick={onAdd}>
          <Plus /> 添加信息源
        </button>
      </div>

      <section className="panel management-list">
        <div className="list-head">
          <span>名称</span>
          <span>入口地址 / 检索意图</span>
          <span>类型与引擎</span>
          <span>启用状态</span>
          <span>操作</span>
        </div>
        {sources.map((source) => {
          const isSearch = source.source_type === 'web_search'
          return (
            <div className="management-row" key={source.id}>
              <b>{source.name}</b>
              <div>
                {isSearch ? (
                  <span className="source-query" title={source.config?.query}>
                    {source.config?.query}
                  </span>
                ) : (
                  <a
                    className="source-link"
                    href={source.base_url}
                    target="_blank"
                    rel="noreferrer"
                    title={source.base_url}
                  >
                    {source.base_url} <ExternalLink />
                  </a>
                )}
              </div>
              <span>
                <span className={`source-kind ${isSearch ? 'web-search' : 'custom'}`}>
                  {isSearch
                    ? '🌐 联网 · Tavily'
                    : source.builtin ? '🏢 内置站点' : '🕷️ 自定义采集'}
                </span>
              </span>
              <div className="source-toggle">
                <label className="switch">
                  <input
                    type="checkbox"
                    checked={source.enabled}
                    onChange={(e) => onToggle(source, e.target.checked)}
                    aria-label={`启用信息源 ${source.name}`}
                  />
                  <i />
                </label>
              </div>
              <div className="source-actions">
                <button className="text-btn" onClick={() => onEdit(source)}>
                  编辑配置
                </button>
                {!source.builtin && (
                  <button
                    className="icon-btn source-delete"
                    type="button"
                    title="删除信息源"
                    aria-label={`删除信息源 ${source.name}`}
                    disabled={deletingIds.has(source.id)}
                    onClick={() => onDelete(source)}
                  >
                    {deletingIds.has(source.id) ? <LoaderCircle className="spin" /> : <Trash2 />}
                  </button>
                )}
              </div>
            </div>
          )
        })}
        {!sources.length && (
          <div className="blank">
            <BookOpen />
            <b>尚未配置信息源</b>
            <span>添加网站爬虫节点或 Tavily 联网检索意图后即可开始采集。</span>
          </div>
        )}
      </section>
    </>
  )
}

function RawArticleModal({ article, onClose }) {
  const [copied, setCopied] = useState(false)

  const copyText = () => {
    if (!article.body) return
    navigator.clipboard.writeText(article.body)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <Modal title="原始数据详情" onClose={onClose} wide>
      <div className="modal-body raw-detail">
        <div className="raw-detail-head">
          <div>
            <h3>{article.title}</h3>
            <p>
              <span>{article.source_name}</span> ·{' '}
              <span>{article.published_at || article.published_text || '日期未知'}</span> ·{' '}
              <span>任务 #{article.task_id || '—'}</span>
            </p>
          </div>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <button className="secondary compact-btn" onClick={copyText}>
              {copied ? <Check /> : <Copy />} {copied ? '已复制' : '复制正文'}
            </button>
            <a className="external button-link" href={article.canonical_url} target="_blank" rel="noreferrer">
              打开原文 <ExternalLink />
            </a>
          </div>
        </div>
        <pre className="raw-body-content">{article.body}</pre>
      </div>
    </Modal>
  )
}

function RawResults({ articles, query, setQuery, onView, onStructure, structuringIds, onDelete }) {
  const [selected, setSelected] = useState([])
  const items = articles.items || []
  const allSelected = items.length > 0 && selected.length === items.length

  const toggle = (id) => {
    setSelected((old) => old.includes(id) ? old.filter((item) => item !== id) : [...old, id])
  }

  const remove = () => {
    if (selected.length && window.confirm('删除原始数据将同时级联删除对应的结构化记录，是否确认删除？')) {
      onDelete(selected)
      setSelected([])
    }
  }

  return (
    <section className="panel results-panel">
      <div className="results-head">
        <div>
          <h2>原始资讯快照</h2>
          <p>共 {articles.total ?? 0} 篇已存档原文数据</p>
        </div>
        <div className="results-actions">
          {selected.length > 0 && (
            <button className="secondary compact-btn" onClick={remove}>
              <Trash2 /> 删除 {selected.length} 篇
            </button>
          )}
          <div className="search-field history-search">
            <Search />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="全文检索文章标题、正文或网址"
            />
            {query && (
              <button
                type="button"
                className="icon-btn"
                style={{ width: 22, height: 22 }}
                onClick={() => setQuery('')}
              >
                <X style={{ width: 14, height: 14 }} />
              </button>
            )}
          </div>
        </div>
      </div>

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th style={{ width: 44 }}>
                <input
                  type="checkbox"
                  checked={allSelected}
                  onChange={() => setSelected(allSelected ? [] : items.map((item) => item.id))}
                />
              </th>
              <th>资讯标题</th>
              <th>信息来源</th>
              <th>发布日期</th>
              <th>采集时间</th>
              <th>结构化状态</th>
              <th>生成记录</th>
              <th>原文</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {items.map((article) => {
              const canStructure = article.status !== 'completed' && article.record_count === 0
              const isStructuring = structuringIds.has(article.id)
              return (
                <tr key={article.id}>
                  <td>
                    <input
                      type="checkbox"
                      checked={selected.includes(article.id)}
                      onChange={() => toggle(article.id)}
                    />
                  </td>
                  <td className="article-title-cell">
                    <button className="text-btn strong-cell" onClick={() => onView(article)}>
                      {article.title}
                    </button>
                  </td>
                  <td>{article.source_name}</td>
                  <td>{article.published_at || article.published_text || '—'}</td>
                  <td>{formatTime(article.collected_at)}</td>
                  <td>
                    <span className={`status ${article.status}`}>
                      {article.status === 'completed'
                        ? '已结构化'
                        : article.status === 'review_required'
                        ? '处理失败'
                        : '待结构化'}
                    </span>
                  </td>
                  <td>
                    <b>{article.record_count}</b> 条
                  </td>
                  <td>
                    <a className="external" href={article.canonical_url} target="_blank" rel="noreferrer">
                      链接 <ExternalLink />
                    </a>
                  </td>
                  <td>
                    {canStructure ? (
                      <button
                        className="primary compact-btn"
                        disabled={isStructuring}
                        onClick={() => onStructure(article.id)}
                      >
                        {isStructuring ? <LoaderCircle className="spin" /> : <Sparkles />}
                        {isStructuring ? '解析中' : 'AI结构化'}
                      </button>
                    ) : (
                      <button className="text-btn" onClick={() => onView(article)}>
                        <Eye /> 查看全文
                      </button>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {!articles.items?.length && (
        <div className="blank">
          <ListFilter />
          <b>暂无原始数据</b>
          <span>完成采集任务后，抓取到的原文快照将保存在这里。</span>
        </div>
      )}
    </section>
  )
}

function HistoryView({
  meta, tasks, records, filters, setFilters, articles,
  articleQuery, setArticleQuery, onLogs, onTerminate, stoppingIds,
  onDetail, onStructure, structuringIds, onDeleteRecords,
  onDeleteArticles, onDeleteTasks
}) {
  const [tab, setTab] = useState('structured')
  const [rawDetail, setRawDetail] = useState(null)
  const [selectedTasks, setSelectedTasks] = useState([])
  const allTasksSelected = tasks.length > 0 && selectedTasks.length === tasks.length

  const removeTasks = () => {
    if (selectedTasks.length && window.confirm(`确定删除选中的 ${selectedTasks.length} 个采集任务记录吗？`)) {
      onDeleteTasks(selectedTasks)
      setSelectedTasks([])
    }
  }

  return (
    <>
      <div className="title-row">
        <div className="title-content">
          <span className="eyebrow">本地数据档案</span>
          <h1>数据归档</h1>
          <p>多维检索、核对要素级结构化数据与爬虫原始全文快照。</p>
        </div>
      </div>

      <div className="data-tabs" role="tablist">
        <button
          className={tab === 'structured' ? 'active' : ''}
          onClick={() => setTab('structured')}
        >
          <Database /> 结构化数据 <span>{records.total ?? 0}</span>
        </button>
        <button
          className={tab === 'raw' ? 'active' : ''}
          onClick={() => setTab('raw')}
        >
          <FileJson /> 原始数据 <span>{articles.total ?? 0}</span>
        </button>
        <button
          className={tab === 'tasks' ? 'active' : ''}
          onClick={() => setTab('tasks')}
        >
          <History /> 采集任务 <span>{tasks.length}</span>
        </button>
      </div>

      {tab === 'structured' ? (
        <Results {...{ meta, records, filters, setFilters }} onDetail={onDetail} onDelete={onDeleteRecords} />
      ) : tab === 'raw' ? (
        <RawResults
          {...{ articles, query: articleQuery, setQuery: setArticleQuery, onStructure, structuringIds }}
          onView={setRawDetail}
          onDelete={onDeleteArticles}
        />
      ) : (
        <section className="panel history-table">
          <div className="results-head">
            <div>
              <h2>采集任务记录</h2>
              <p>共 {tasks.length} 条调度执行记录</p>
            </div>
            {selectedTasks.length > 0 && (
              <button className="secondary compact-btn" onClick={removeTasks}>
                <Trash2 /> 删除 {selectedTasks.length} 条
              </button>
            )}
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th style={{ width: 44 }}>
                    <input
                      type="checkbox"
                      checked={allTasksSelected}
                      onChange={() => setSelectedTasks(allTasksSelected ? [] : tasks.map((t) => t.id))}
                    />
                  </th>
                  <th>任务 ID</th>
                  <th>起始日期</th>
                  <th>采集信息源</th>
                  <th>状态</th>
                  <th>保存原文 / 结构化 / 失败</th>
                  <th>创建时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {tasks.map((task) => (
                  <tr key={task.id}>
                    <td>
                      <input
                        type="checkbox"
                        checked={selectedTasks.includes(task.id)}
                        onChange={() => setSelectedTasks((old) =>
                          old.includes(task.id) ? old.filter((item) => item !== task.id) : [...old, task.id]
                        )}
                      />
                    </td>
                    <td className="strong-cell">#{task.id}</td>
                    <td>{task.start_date}</td>
                    <td>{task.source_snapshot.map((item) => item.name).join('、')}</td>
                    <td>
                      <span className={`status ${task.status}`}>
                        {taskStatusLabel(task.status)}
                      </span>
                    </td>
                    <td>
                      <b>{task.fetched_count}</b> / <b>{task.structured_count}</b> / <b>{task.failed_count}</b>
                    </td>
                    <td>{formatTime(task.created_at)}</td>
                    <td>
                      <div className="row-actions">
                        <button className="text-btn" onClick={() => onLogs(task.id)}>
                          运行日志
                        </button>
                        {['queued', 'running'].includes(task.status) && (
                          <button
                            className="text-btn danger-text"
                            disabled={stoppingIds.has(task.id)}
                            onClick={() => onTerminate(task.id)}
                          >
                            <Square /> {stoppingIds.has(task.id) ? '终止中' : '终止'}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!tasks.length && (
            <div className="blank">
              <History />
              <b>暂无任务记录</b>
            </div>
          )}
        </section>
      )}

      {rawDetail && <RawArticleModal article={rawDetail} onClose={() => setRawDetail(null)} />}
    </>
  )
}

function RecordDetailModal({ id, meta, onClose, onSaved }) {
  const [record, setRecord] = useState(null)
  const [form, setForm] = useState(null)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    api.record(id)
      .then((data) => {
        setRecord(data)
        setForm(data)
      })
      .catch((err) => setError(err.message))
  }, [id])

  const save = async () => {
    setSaving(true)
    try {
      const fields = [
        'region', 'organization', 'company_name', 'event_date',
        'info_type', 'investment_amount', 'project_name', 'source_name',
        'original_url', 'details'
      ]
      const saved = await api.updateRecord(
        id,
        Object.fromEntries(fields.map((field) => [field, form[field] || (field === 'event_date' ? null : '')]))
      )
      onSaved(saved)
      onClose()
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal title="结构化要素记录审核与编辑" onClose={onClose} wide>
      <div className="modal-body">
        {!form ? (
          <div className="loading">
            <LoaderCircle className="spin" />
            <span>正在加载记录详情...</span>
          </div>
        ) : (
          <div className="detail-layout">
            <div className="detail-main">
              <div className="form-grid">
              <label className="field">
                <span>地域 / 城市</span>
                <input
                  value={form.region || ''}
                  onChange={(e) => setForm({ ...form, region: e.target.value })}
                  placeholder="例如：上海、江苏无锡"
                />
              </label>
              <label className="field">
                <span>开发区 / 产业园区 / 院校</span>
                <input
                  value={form.organization || ''}
                  onChange={(e) => setForm({ ...form, organization: e.target.value })}
                  placeholder="例如：张江高科技园区"
                />
              </label>
              <label className="field">
                <span>企业名称</span>
                <input
                  value={form.company_name || ''}
                  onChange={(e) => setForm({ ...form, company_name: e.target.value })}
                  placeholder="例如：长电科技"
                />
              </label>
              <label className="field">
                <span>资讯类型</span>
                <select
                  value={form.info_type || ''}
                  onChange={(e) => setForm({ ...form, info_type: e.target.value })}
                >
                  <option value="">请选择资讯类型</option>
                  {(meta.info_types || []).map((t) => (
                    <option key={t} value={t}>{t}</option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>事件日期</span>
                <input
                  type="date"
                  value={form.event_date || ''}
                  onChange={(e) => setForm({ ...form, event_date: e.target.value })}
                />
              </label>
              <label className="field">
                <span>投资金额 / 产能规模</span>
                <input
                  value={form.investment_amount || ''}
                  onChange={(e) => setForm({ ...form, investment_amount: e.target.value })}
                  placeholder="例如：50亿元人民币、月产10万片"
                />
              </label>
              <label className="field full">
                <span>产品 / 产线 / 项目全称</span>
                <input
                  value={form.project_name || ''}
                  onChange={(e) => setForm({ ...form, project_name: e.target.value })}
                  placeholder="例如：先进封装测试生产基地二期项目"
                />
              </label>
              <label className="field">
                <span>信息源名称</span>
                <input
                  value={form.source_name || ''}
                  onChange={(e) => setForm({ ...form, source_name: e.target.value })}
                />
              </label>
              <label className="field">
                <span>原文链接</span>
                <input
                  type="url"
                  value={form.original_url || ''}
                  onChange={(e) => setForm({ ...form, original_url: e.target.value })}
                />
              </label>
              <label className="field full">
                <span>事件详细描述与产业背景</span>
                <textarea
                  rows={6}
                  value={form.details || ''}
                  onChange={(e) => setForm({ ...form, details: e.target.value })}
                  placeholder="记录该资讯核心要素、产业影响、签约细节等"
                />
              </label>
            </div>
            {error && <p className="form-error"><AlertCircle /> {error}</p>}
            </div>

            <aside className="detail-aside">
              <h3><ShieldCheck /> 溯源信息卡</h3>
              <div className="detail-chips">
                {form.region && <span className="region-chip"><MapPin /> {form.region}</span>}
                {form.info_type && (
                  <span className="type-chip" data-type={form.info_type}>{form.info_type}</span>
                )}
              </div>
              <div className="detail-summary">
                <div className="detail-summary-item">
                  <span>记录编号</span>
                  <b>#{id}</b>
                </div>
                <div className="detail-summary-item">
                  <span>信息来源</span>
                  <b>{form.source_name || '未标注来源'}</b>
                </div>
                <div className="detail-summary-item">
                  <span>事件日期</span>
                  <b>{form.event_date || '未提取到日期'}</b>
                </div>
                <div className="detail-summary-item">
                  <span>投资金额 / 产能</span>
                  <b>{form.investment_amount || '未披露'}</b>
                </div>
                <div className="detail-summary-item">
                  <span>入库时间</span>
                  <b>{formatTime(record?.created_at)}</b>
                </div>
              </div>
              {form.original_url ? (
                <a className="secondary compact-btn" href={form.original_url} target="_blank" rel="noreferrer">
                  <ExternalLink /> 打开原文核对
                </a>
              ) : (
                <p className="field-note"><Info /> 该记录缺少原文链接，建议补全以便追溯核验。</p>
              )}
            </aside>
          </div>
        )}
      </div>
      <footer className="modal-foot">
        <button type="button" className="secondary" onClick={onClose}>取消</button>
        <button className="primary" onClick={save} disabled={saving || !form}>
          {saving ? <LoaderCircle className="spin" /> : <Save />}
          {saving ? '保存中...' : '保存要素修改'}
        </button>
      </footer>
    </Modal>
  )
}

function SettingsView() {
  const [form, setForm] = useState({
    base_url: DASHSCOPE_BASE_URL,
    model_name: 'qwen3-max',
    api_key: '',
    request_headers: [],
    tavily_api_key: '',
    enabled: false
  })
  const [saved, setSaved] = useState(null)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [feedback, setFeedback] = useState('')

  useEffect(() => {
    api.modelSetting()
      .then((data) => {
        setSaved(data)
        setForm((old) => ({ ...old, ...data, api_key: '' }))
      })
      .catch((err) => setError(err.message))
  }, [])

  const submit = async (event) => {
    event.preventDefault()
    setError('')
    setFeedback('')
    setSaving(true)
    try {
      const result = await api.saveModelSetting(form)
      setSaved(result)
      setForm({ ...form, api_key: '' })
      setFeedback('模型服务配置已成功保存！')
      setTimeout(() => setFeedback(''), 3500)
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  const addHeader = () => {
    setForm({ ...form, request_headers: [...form.request_headers, { key: '', value: '' }] })
  }

  const updateHeader = (index, field, value) => {
    setForm({
      ...form,
      request_headers: form.request_headers.map((header, itemIndex) =>
        itemIndex === index ? { ...header, [field]: value } : header
      )
    })
  }

  const removeHeader = (index) => {
    setForm({
      ...form,
      request_headers: form.request_headers.filter((_, itemIndex) => itemIndex !== index)
    })
  }

  return (
    <>
      <div className="title-row">
        <div className="title-content">
          <span className="eyebrow">服务端基础设施</span>
          <h1>API 与模型配置</h1>
          <p>管理 OpenAI 兼容大模型接入点与 Tavily 联网搜索服务。</p>
        </div>
      </div>

      <div className="settings-grid">
        <section className="panel model-form">
          <div className="panel-head">
            <div>
              <h2>AI 模型服务 (DashScope / OpenAI Compatible)</h2>
              <p>驱动资讯摘要、三维关键词过滤与实体要素提取的核心引擎</p>
            </div>
            <SlidersHorizontal />
          </div>

          <form onSubmit={submit}>
            <div className="form-grid">
              <label className="field">
                <span>API 接口地址 (Base URL)</span>
                <input
                  type="url"
                  required
                  value={form.base_url}
                  onChange={(e) => setForm({ ...form, base_url: e.target.value })}
                />
              </label>
              <label className="field">
                <span>模型标识 (Model Name)</span>
                <input
                  required
                  value={form.model_name}
                  onChange={(e) => setForm({ ...form, model_name: e.target.value })}
                />
              </label>
              <label className="field full">
                <span>
                  API 密钥 (API Key)
                  {saved?.has_api_key && (
                    <small style={{ marginLeft: 8, color: 'var(--emerald-600)' }}>
                      ✓ 已保存密钥：{saved.api_key_hint}
                    </small>
                  )}
                </span>
                <input
                  type="password"
                  value={form.api_key}
                  onChange={(e) => setForm({ ...form, api_key: e.target.value })}
                  placeholder={saved?.has_api_key ? '留空以继续使用现有密钥' : 'sk-...'}
                />
              </label>
              <label className="field full">
                <span>Tavily API 密钥 {saved?.has_tavily_api_key && <small style={{ marginLeft: 8, color: 'var(--emerald-600)' }}>已保存：{saved.tavily_api_key_hint}</small>}</span>
                <input type="password" value={form.tavily_api_key || ''} onChange={(e) => setForm({ ...form, tavily_api_key: e.target.value })} placeholder={saved?.has_tavily_api_key ? '留空以继续使用现有密钥' : 'tvly-...'} />
              </label>

              <div className="header-config full">
                <div className="header-config-title">
                  <h3>自定义 HTTP 请求头</h3>
                  <button type="button" className="secondary compact-btn" onClick={addHeader}>
                    <Plus /> 添加请求头
                  </button>
                </div>
                {form.request_headers.length > 0 && (
                  <div className="header-labels">
                    <span>Header 键</span>
                    <span>Header 值</span>
                    <span />
                  </div>
                )}
                <div className="header-rows">
                  {form.request_headers.map((header, index) => (
                    <div className="header-row" key={index}>
                      <input
                        required
                        aria-label={`请求头 ${index + 1} 键`}
                        placeholder="例如：X-Custom-Auth"
                        value={header.key}
                        onChange={(e) => updateHeader(index, 'key', e.target.value)}
                      />
                      <input
                        required
                        aria-label={`请求头 ${index + 1} 值`}
                        placeholder="请求头内容"
                        value={header.value}
                        onChange={(e) => updateHeader(index, 'value', e.target.value)}
                      />
                      <button
                        type="button"
                        className="icon-btn"
                        title="删除请求头"
                        aria-label={`删除请求头 ${index + 1}`}
                        onClick={() => removeHeader(index)}
                      >
                        <Trash2 />
                      </button>
                    </div>
                  ))}
                </div>
              </div>

              <label className="check-field full">
                <input
                  type="checkbox"
                  checked={form.enabled}
                  onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
                />
                <span>爬虫采集时启用普通网站原文自动结构化</span>
              </label>

              {feedback && (
                <p className="form-success full">
                  <CheckCircle /> {feedback}
                </p>
              )}
              {error && (
                <p className="form-error full">
                  <AlertCircle /> {error}
                </p>
              )}
            </div>

            <footer style={{ marginTop: 20 }}>
              <button className="primary" disabled={saving}>
                {saving ? <LoaderCircle className="spin" /> : <Save />}
                {saving ? '正在保存...' : '保存模型配置'}
              </button>
            </footer>
          </form>
        </section>
      </div>
    </>
  )
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
  const [filters, setFilters] = useState({
    q: '', region: '', info_type: [], source: '', date_from: '', date_to: ''
  })
  const [articles, setArticles] = useState({ items: [], total: 0 })
  const [articleQuery, setArticleQuery] = useState('')
  const [structuringIds, setStructuringIds] = useState(() => new Set())
  const [stoppingIds, setStoppingIds] = useState(() => new Set())
  const [deletingSourceIds, setDeletingSourceIds] = useState(() => new Set())
  const [sourceModal, setSourceModal] = useState(null)
  const [logs, setLogs] = useState(null)
  const [detailId, setDetailId] = useState(null)
  const [creating, setCreating] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [keywordSetting, setKeywordSetting] = useState({
    keyword_config: [], keyword_filter_enabled: false, enabled: false
  })

  const load = useCallback(async () => {
    try {
      const [metaData, sourceData, taskData, settingData, recentRecords] = await Promise.all([
        api.meta(),
        api.sources(),
        api.tasks(),
        api.modelSetting(),
        api.records({ limit: 5 })
      ])
      setMeta(metaData)
      setSources(sourceData)
      setTasks(taskData)
      setKeywordSetting(settingData)
      setDashboardRecords(recentRecords)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  const refreshHistory = useCallback(async () => {
    try {
      const [recordData, articleData] = await Promise.all([
        api.records(filters),
        api.articles({ q: articleQuery })
      ])
      setRecords(recordData)
      setArticles(articleData)
    } catch (err) {
      setError(err.message)
    }
  }, [filters, articleQuery])

  const refreshAnalytics = useCallback(async () => {
    setAnalyticsLoading(true)
    try {
      setAnalytics(await api.analytics(filters))
    } catch (err) {
      setError(err.message)
    } finally {
      setAnalyticsLoading(false)
    }
  }, [filters])

  const refreshAll = useCallback(async () => {
    await load()
    await refreshHistory()
    if (view === 'analytics') await refreshAnalytics()
  }, [load, refreshHistory, refreshAnalytics, view])

  useEffect(() => {
    load()
  }, [load])

  // Keep running task counters and newly persisted articles visible while collection is active.
  useEffect(() => {
    if (!tasks.some((task) => ['queued', 'running', 'terminating'].includes(task.status))) return undefined
    let disposed = false
    const poll = async () => {
      try {
        const taskData = await api.tasks()
        if (disposed) return
        setTasks(taskData)
        if (view === 'history' || view === 'dashboard') {
          const articleData = await api.articles({ q: articleQuery })
          if (!disposed) setArticles(articleData)
        }
      } catch (err) {
        if (!disposed) setError(err.message)
      }
    }
    const timer = window.setInterval(poll, 2000)
    return () => {
      disposed = true
      window.clearInterval(timer)
    }
  }, [tasks, view, articleQuery])

  useEffect(() => {
    if (view === 'history' || view === 'dashboard') {
      refreshHistory()
    }
    if (view === 'analytics') {
      refreshAnalytics()
    }
  }, [view, refreshHistory, refreshAnalytics])

  const createTask = async (payload) => {
    setCreating(true)
    setError('')
    try {
      await api.createTask(payload)
      await refreshAll()
    } catch (err) {
      setError(err.message)
    } finally {
      setCreating(false)
    }
  }

  const showLogs = async (id) => {
    setError('')
    try {
      setLogs(await api.logs(id))
    } catch (err) {
      setError(err.message)
    }
  }

  const terminateTask = async (id) => {
    setStoppingIds((old) => new Set(old).add(id))
    setError('')
    try {
      await api.terminateTask(id)
      await refreshAll()
    } catch (err) {
      setError(err.message)
    } finally {
      setStoppingIds((old) => {
        const next = new Set(old)
        next.delete(id)
        return next
      })
    }
  }

  const sourceSaved = (saved) => {
    setSources((old) =>
      old.some((item) => item.id === saved.id)
        ? old.map((item) => (item.id === saved.id ? saved : item))
        : [...old, saved]
    )
  }

  const toggleSource = async (source, enabled) => {
    try {
      sourceSaved(await api.updateSource(source.id, { enabled }))
    } catch (err) {
      setError(err.message)
    }
  }

  const deleteSource = async (source) => {
    if (!window.confirm(`确定删除信息源“${source.name}”吗？此操作不会删除已产生的历史任务与记录。`)) return
    setDeletingSourceIds((old) => new Set(old).add(source.id))
    setError('')
    try {
      await api.deleteSource(source.id)
      setSources((old) => old.filter((item) => item.id !== source.id))
    } catch (err) {
      setError(err.message)
    } finally {
      setDeletingSourceIds((old) => {
        const next = new Set(old)
        next.delete(source.id)
        return next
      })
    }
  }

  const structureArticle = async (id) => {
    setStructuringIds((old) => new Set(old).add(id))
    setError('')
    try {
      await api.structureArticle(id)
      const [articleData, recordData] = await Promise.all([
        api.articles({ q: articleQuery }),
        api.records(filters)
      ])
      setArticles(articleData)
      setRecords(recordData)
    } catch (err) {
      setError(err.message)
    } finally {
      setStructuringIds((old) => {
        const next = new Set(old)
        next.delete(id)
        return next
      })
    }
  }

  const deleteHistory = async (kind, ids) => {
    setError('')
    try {
      if (kind === 'records') await api.deleteRecords(ids)
      else if (kind === 'articles') await api.deleteArticles(ids)
      else await api.deleteTasks(ids)
      await refreshAll()
    } catch (err) {
      setError(err.message)
    }
  }

  const activeLabel = useMemo(() => ALL_NAV.find((item) => item.id === view)?.label, [view])

  return (
    <div className="app-shell">
      <aside className={mobileNav ? 'sidebar open' : 'sidebar'}>
        <div className="brand">
          <div className="brand-icon-wrap">
            <Cpu />
          </div>
          <div className="brand-text">
            <div className="brand-title">
              <b>第二战线情报站</b>
            </div>
            <div className="brand-subtitle">SEMI INTELLIGENCE PLATFORM</div>
          </div>
        </div>

        <nav>
          {NAV_GROUPS.map((group) => (
            <div className="nav-group" key={group.title}>
              <div className="nav-group-label">{group.title}</div>
              {group.items.map(({ id, label, icon: Icon }) => (
                <button
                  key={id}
                  className={view === id ? 'active' : ''}
                  onClick={() => {
                    setView(id)
                    setMobileNav(false)
                  }}
                >
                  <Icon />
                  <span>{label}</span>
                </button>
              ))}
            </div>
          ))}
        </nav>

        <div className="service-state">
          <div className="service-indicator" />
          <div className="service-info">
            <b>本地服务在线</b>
            <small>Tavily 搜索服务</small>
          </div>
        </div>
      </aside>

      <div className="main-column">
        <header className="topbar">
          <div className="topbar-left">
            <button
              className="mobile-menu icon-btn"
              onClick={() => setMobileNav(!mobileNav)}
              aria-label="菜单"
            >
              <Menu />
            </button>
            <div className="topbar-breadcrumb">
              <span>系统工作区</span>
              <ChevronRight style={{ width: 14, height: 14 }} />
              <strong>{activeLabel}</strong>
            </div>
          </div>

          <div className="topbar-right">
            <div className="topbar-badge">
              <span className="dot" />
              <span>系统运行正常</span>
            </div>
            <button className="icon-btn" aria-label="刷新数据" onClick={refreshAll} title="刷新全局数据">
              <RefreshCw />
            </button>
          </div>
        </header>

        <main>
          {loading ? (
            <div className="loading">
              <LoaderCircle className="spin" />
              <span>正在连接本地半导体情报服务...</span>
            </div>
          ) : view === 'dashboard' ? (
            <Dashboard
              {...{ meta, sources, tasks, articles, creating, keywordSetting, stoppingIds }}
              records={dashboardRecords}
              onCreateTask={createTask}
              onAddSource={() => setSourceModal('new')}
              onLogs={showLogs}
              onTerminate={terminateTask}
              onDetail={(id) => setDetailId(id)}
              onHistory={() => setView('history')}
              onError={setError}
            />
          ) : view === 'sources' ? (
            <SourcesView
              sources={sources}
              onAdd={() => setSourceModal('new')}
              onEdit={setSourceModal}
              onToggle={toggleSource}
              onDelete={deleteSource}
              deletingIds={deletingSourceIds}
            />
          ) : view === 'keywords' ? (
            <KeywordsView setting={keywordSetting} onSaved={setKeywordSetting} />
          ) : view === 'history' ? (
            <HistoryView
              {...{
                meta, tasks, records, filters, setFilters, articles,
                articleQuery, setArticleQuery, structuringIds, stoppingIds
              }}
              onLogs={showLogs}
              onTerminate={terminateTask}
              onDetail={setDetailId}
              onStructure={structureArticle}
              onDeleteRecords={(ids) => deleteHistory('records', ids)}
              onDeleteArticles={(ids) => deleteHistory('articles', ids)}
              onDeleteTasks={(ids) => deleteHistory('tasks', ids)}
            />
          ) : view === 'analytics' ? (
            <AnalyticsView
              data={analytics}
              loading={analyticsLoading}
              filters={filters}
              setFilters={setFilters}
            />
          ) : (
            <SettingsView />
          )}
        </main>
      </div>

      {mobileNav && (
        <button
          className="nav-backdrop"
          aria-label="关闭菜单"
          onClick={() => setMobileNav(false)}
        />
      )}

      {sourceModal && (
        <SourceForm
          source={sourceModal === 'new' ? null : sourceModal}
          onClose={() => setSourceModal(null)}
          onSaved={sourceSaved}
        />
      )}

      {logs && (
        <Modal title="任务运行日志" onClose={() => setLogs(null)} wide>
          <div className="log-modal-content">
            <div className="log-list">
              {logs.map((log) => (
                <div key={log.id} className={`log-entry ${log.level.toLowerCase()}`}>
                  <time>{formatTime(log.created_at)}</time>
                  <span className={`log-badge ${log.level}`}>{log.level}</span>
                  <p>{log.message}</p>
                </div>
              ))}
              {!logs.length && (
                <div className="blank compact">
                  <History />
                  <b>暂无日志记录</b>
                </div>
              )}
            </div>
          </div>
        </Modal>
      )}

      {detailId && (
        <RecordDetailModal
          id={detailId}
          meta={meta}
          onClose={() => setDetailId(null)}
          onSaved={(saved) =>
            setRecords((old) => ({
              ...old,
              items: old.items.map((item) => (item.id === saved.id ? saved : item))
            }))
          }
        />
      )}

      {error && (
        <div className="toast" role="alert">
          <AlertCircle style={{ width: 18, height: 18, flexShrink: 0 }} />
          <span>{error}</span>
          <button className="icon-btn" onClick={() => setError('')} aria-label="关闭提示">
            <X style={{ width: 14, height: 14 }} />
          </button>
        </div>
      )}
    </div>
  )
}
