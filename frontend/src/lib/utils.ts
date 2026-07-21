import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") || ""

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function apiUrl(endpoint: string) {
  if (/^https?:\/\//.test(endpoint)) {
    return endpoint
  }

  return `${API_BASE_URL}${endpoint.startsWith("/") ? endpoint : `/${endpoint}`}`
}

export function getErrorMessage(error: unknown, fallback = "Something went wrong") {
  return error instanceof Error ? error.message : fallback
}

export function isAbortError(error: unknown) {
  if (error instanceof DOMException) return error.name === "AbortError" || error.name === "TimeoutError"
  if (!(error instanceof Error)) return false
  if (error.name === "AbortError" || error.name === "TimeoutError") return true
  const message = error.message.toLowerCase()
  return message.includes("aborterror") || message.includes("aborted") || message.includes("signal is aborted")
}

export function formatApiDetail(detail: unknown, fallback = "Something went wrong") {
  if (!detail) return fallback
  if (typeof detail === "string") return detail
  if (typeof detail === "object" && detail !== null) {
    const data = detail as { message?: string; required?: number; balance?: number; action?: string }
    if (data.message === "Insufficient credits") {
      return `Insufficient credits: ${data.required ?? "?"} required, ${data.balance ?? 0} available.`
    }
    if (data.message) return data.message
  }
  return fallback
}

const LEGACY_READ_ONLY_SURFACE_SELECTOR = '[data-legacy-readonly="true"]'
const TRUTHY_QUERY_VALUES = new Set(["1", "true", "yes", "on"])
const CSRF_COOKIE_NAME = "autoleadgen_csrf"

function readCookie(name: string) {
  if (typeof document === "undefined") return null
  const prefix = `${encodeURIComponent(name)}=`
  const item = document.cookie.split("; ").find(value => value.startsWith(prefix))
  return item ? decodeURIComponent(item.slice(prefix.length)) : null
}

export class LegacyReadOnlyRequestError extends Error {
  readonly code = "LEGACY_API_READ_ONLY"

  constructor(method: string, endpoint: string) {
    super(`Legacy read-only surface blocked ${method} ${endpoint}`)
    this.name = "LegacyReadOnlyRequestError"
  }
}

function isTruthyQueryFlag(url: URL, name: string) {
  return TRUTHY_QUERY_VALUES.has((url.searchParams.get(name) ?? "").trim().toLowerCase())
}

function hasLegacyGetSideEffects(url: URL) {
  const path = url.pathname.replace(/\/+$/, "") || "/"

  return path === "/api/auth/users"
    || path === "/api/credits/me"
    || path === "/api/credits/transactions"
    || path.startsWith("/api/credits/users/")
    || path === "/api/api-usage/summary"
    || (path === "/api/channels/accounts" && isTruthyQueryFlag(url, "sync"))
    || (path === "/api/health/status" && isTruthyQueryFlag(url, "external"))
    || /^\/api\/workflows\/[^/]+\/health$/.test(path)
}

function isLegacyReadOnlySurfaceActive() {
  return typeof document !== "undefined"
    && document.querySelector(LEGACY_READ_ONLY_SURFACE_SELECTOR) !== null
}

function legacyReadOnlyBlock(endpoint: string, options: RequestInit) {
  if (!isLegacyReadOnlySurfaceActive()) return null

  const url = new URL(apiUrl(endpoint), typeof window === "undefined" ? "http://localhost" : window.location.origin)
  const path = url.pathname.replace(/\/+$/, "") || "/"
  const isLegacyApi = path.startsWith("/api/") && !path.startsWith("/api/v2/")
  if (!isLegacyApi) return null
  if (path === "/api/auth/logout") return null

  const method = (options.method ?? "GET").toUpperCase()
  const isSafeMethod = method === "GET" || method === "HEAD" || method === "OPTIONS"
  if (!isSafeMethod || (method === "GET" && hasLegacyGetSideEffects(url))) {
    return new LegacyReadOnlyRequestError(method, `${path}${url.search}`)
  }
  return null
}

export async function apiFetch(endpoint: string, options: RequestInit = {}) {
  const readOnlyBlock = legacyReadOnlyBlock(endpoint, options)
  if (readOnlyBlock) {
    return new Response(JSON.stringify({
      detail: { code: readOnlyBlock.code, message: readOnlyBlock.message },
    }), {
      status: 409,
      headers: { "Content-Type": "application/json" },
    })
  }

  const token = typeof window !== 'undefined' ? localStorage.getItem('auth_token') : null;
  const bodyIsFormData = typeof FormData !== "undefined" && options.body instanceof FormData
  const headers = new Headers(options.headers)

  if (!bodyIsFormData && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json")
  }

  if (token) {
    headers.set("Authorization", `Bearer ${token}`)
  }

  const method = (options.method ?? "GET").toUpperCase()
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && !headers.has("X-CSRF-Token")) {
    const csrfToken = readCookie(CSRF_COOKIE_NAME)
    if (csrfToken) headers.set("X-CSRF-Token", csrfToken)
  }

  const res = await fetch(apiUrl(endpoint), {
    ...options,
    headers,
    credentials: "include",
  });

  if (res.status === 401) {
    if (typeof window !== 'undefined') {
      localStorage.removeItem('auth_token');
      localStorage.removeItem('auth_user');
      window.location.href = '/login';
    }
    throw new Error('Unauthorized');
  }

  return res;
}
