// 统一 fetch: 带 cookie; 401 → 跳登录页。
export async function apiFetch(input: string, init?: RequestInit): Promise<Response> {
  const res = await fetch(input, { ...init, credentials: 'include' })
  if (res.status === 401 && typeof window !== 'undefined'
      && !window.location.pathname.startsWith('/login')) {
    window.location.href = '/login'
  }
  return res
}
