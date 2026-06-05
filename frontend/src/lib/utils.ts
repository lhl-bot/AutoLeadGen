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
  return error instanceof DOMException
    ? error.name === "AbortError" || error.name === "TimeoutError"
    : error instanceof Error && (error.name === "AbortError" || error.name === "TimeoutError")
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

export async function apiFetch(endpoint: string, options: RequestInit = {}) {
  const token = typeof window !== 'undefined' ? localStorage.getItem('auth_token') : null;
  const bodyIsFormData = typeof FormData !== "undefined" && options.body instanceof FormData
  const headers = new Headers(options.headers)

  if (!bodyIsFormData && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json")
  }

  if (token) {
    headers.set("Authorization", `Bearer ${token}`)
  }

  const res = await fetch(apiUrl(endpoint), {
    ...options,
    headers,
  });

  if (res.status === 401) {
    if (typeof window !== 'undefined') {
      localStorage.removeItem('auth_token');
      window.location.href = '/login';
    }
    throw new Error('Unauthorized');
  }

  return res;
}
