import axios from 'axios'

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

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('opskg_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

api.interceptors.response.use(
  (response) => response.data,
  (error) => {
    console.error('API Error:', error.response?.data?.detail || error.message)
    return Promise.reject(error)
  },
)

export default api
