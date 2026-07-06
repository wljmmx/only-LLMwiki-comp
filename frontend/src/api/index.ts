import axios from 'axios'
import {
  startLoadingBar,
  finishLoadingBar,
  errorLoadingBar,
} from './loadingBar'

const API_BASE_URL = '/api'

export function getApiBaseUrl() {
  return API_BASE_URL
}

const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
})

api.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('opskg_token')
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    // S14-2: 全局 loading bar（每个请求启动进度条）
    startLoadingBar()
    return config
  },
  (error) => {
    // 请求发送失败（如网络错误）也要结束 loading bar
    errorLoadingBar()
    return Promise.reject(error)
  },
)

api.interceptors.response.use(
  (response) => {
    // S14-2: 请求成功 → 完成 loading bar
    finishLoadingBar()
    return response.data
  },
  (error) => {
    // S14-2: 请求失败 → 错误 loading bar
    errorLoadingBar()
    console.error('API Error:', error.response?.data?.detail || error.message)
    return Promise.reject(error)
  },
)

export default api
