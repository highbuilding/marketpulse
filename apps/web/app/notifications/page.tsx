import { redirect } from 'next/navigation'

// 旧路由:信号浏览已迁到 /signals,配置已迁到 /settings/notifications。
// 保留此路由做重定向,避免旧链接 404。
export default function NotificationsRedirect() {
  redirect('/signals')
}
