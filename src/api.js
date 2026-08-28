const API = '/api'
const SENSITIVE_KEY = /(?:api[_-]?key|authorization|password|secret|token)/i

function sanitize(value) {
  if (Array.isArray(value)) return value.map(sanitize)
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, SENSITIVE_KEY.test(key) ? '[REDACTED]' : sanitize(item)]))
  }
  return value
}

function requestValue(body) {
  if (typeof body !== 'string') return sanitize(body)
  try { return sanitize(JSON.parse(body)) } catch { return body }
}

function errorMessage(value, fallback) {
  const detail = value && typeof value === 'object' ? value.detail : null
  if (typeof detail === 'string') return detail
  if (detail != null) return JSON.stringify(detail)
  return fallback
}

function logFailure(details) {
  console.error('[API 请求失败]', details)
}

async function request(path, options = {}) {
  const { responseType = 'json', ...fetchOptions } = options
  const url = `${API}${path}`
  const method = fetchOptions.method || 'GET'
  const headers = { 'Content-Type': 'application/json', ...fetchOptions.headers }
  const requestDetails = { method, url, headers: sanitize(headers), body: requestValue(fetchOptions.body) }
  let response
  try {
    response = await fetch(url, { ...fetchOptions, headers })
  } catch (error) {
    logFailure({ ...requestDetails, type: 'network_error', error })
    throw error
  }

  if (response.ok && responseType === 'blob') {
    try { return await response.blob() } catch (error) {
      logFailure({ ...requestDetails, type: 'response_read_error', status: response.status, error })
      throw error
    }
  }

  let responseText
  try {
    responseText = await response.text()
  } catch (error) {
    logFailure({ ...requestDetails, type: 'response_read_error', status: response.status, error })
    throw error
  }
  let responseValue = null
  let responseIsJson = false
  if (responseText) {
    try { responseValue = JSON.parse(responseText); responseIsJson = true } catch { responseValue = responseText }
  }
  if (!response.ok) {
    const fallback = `请求失败 (${response.status})`
    const message = errorMessage(responseValue, fallback)
    logFailure({
      ...requestDetails,
      type: 'http_error',
      status: response.status,
      statusText: response.statusText,
      response: responseValue,
    })
    const error = new Error(message)
    error.status = response.status
    error.response = responseValue
    throw error
  }
  if (!responseText) return null
  if (!responseIsJson) {
    const error = new Error('接口返回的内容不是有效 JSON')
    logFailure({ ...requestDetails, type: 'invalid_json', status: response.status, response: responseValue, error })
    throw error
  }
  return responseValue
}

function queryString(params = {}) {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (Array.isArray(value)) value.filter(Boolean).forEach((item) => query.append(key, item))
    else if (value !== '' && value != null) query.append(key, value)
  })
  return query
}

export const api = {
  meta: () => request('/meta'),
  sources: () => request('/sources'),
  addSource: (source) => request('/sources', { method: 'POST', body: JSON.stringify(source) }),
  updateSource: (id, source) => request(`/sources/${id}`, { method: 'PATCH', body: JSON.stringify(source) }),
  deleteSource: (id) => request(`/sources/${id}`, { method: 'DELETE' }),
  testSource: (source) => request('/sources/test', { method: 'POST', body: JSON.stringify(source) }),
  tasks: () => request('/tasks'),
  createTask: (task) => request('/tasks', { method: 'POST', body: JSON.stringify(task) }),
  task: (id) => request(`/tasks/${id}`),
  terminateTask: (id) => request(`/tasks/${id}/terminate`, { method: 'POST' }),
  logs: (id) => request(`/tasks/${id}/logs`),
  deleteTasks: (ids) => request('/tasks', { method: 'DELETE', body: JSON.stringify({ ids }) }),
  records: (params = {}) => {
    const query = queryString(params)
    return request(`/records?${query}`)
  },
  analytics: (params = {}) => {
    const query = queryString(params)
    return request(`/analytics/overview?${query}`)
  },
  deleteRecords: (ids) => request('/records', { method: 'DELETE', body: JSON.stringify({ ids }) }),
  articles: (params = {}) => {
    const query = new URLSearchParams(Object.entries(params).filter(([, value]) => value !== ''))
    return request(`/articles?${query}`)
  },
  article: (id) => request(`/articles/${id}`),
  deleteArticles: (ids) => request('/articles', { method: 'DELETE', body: JSON.stringify({ ids }) }),
  structureArticle: (id) => request(`/articles/${id}/structure`, { method: 'POST' }),
  record: (id) => request(`/records/${id}`),
  updateRecord: (id, record) => request(`/records/${id}`, { method: 'PATCH', body: JSON.stringify(record) }),
  modelSetting: () => request('/settings/model'),
  saveModelSetting: (setting) => request('/settings/model', { method: 'PUT', body: JSON.stringify(setting) }),
  exportFile: (format, params = {}) => {
    const query = queryString({ format, ...params })
    return request(`/exports?${query}`, { responseType: 'blob' })
  },
}
