import { ApiError, request } from './client'

export interface Member {
  id: string
  email: string
  name: string
  department: string
  job_title: string
  role: 'USER' | 'ADMIN'
  status: 'ACTIVE' | 'PENDING'
  created_at: string
  updated_at: string
}
export interface SignupInput { name: string; email: string; department: string; job_title: string; password: string }

function member(value: { user: Member }): Member {
  const user = value?.user
  if (!user || typeof user.id !== 'string' || typeof user.email !== 'string' || typeof user.name !== 'string'
    || typeof user.department !== 'string' || typeof user.job_title !== 'string'
    || !['USER', 'ADMIN'].includes(user.role) || !['PENDING', 'ACTIVE'].includes(user.status)) {
    throw new ApiError(502, 'INVALID_RESPONSE', 'Invalid member response.', false)
  }
  return user
}
export async function currentMember() { return member(await request<{ user: Member }>('/api/v1/auth/me')) }
export async function login(email: string, password: string) {
  return member(await request<{ user: Member }>('/api/v1/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) }))
}
export async function signup(input: SignupInput) {
  return member(await request<{ user: Member }>('/api/v1/auth/signup', { method: 'POST', body: JSON.stringify(input) }))
}
export function logout() { return request<void>('/api/v1/auth/logout', { method: 'POST' }) }
