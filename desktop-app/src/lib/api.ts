import { API_BASE } from './config';

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, token: string | null, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      ...(init?.headers || {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
    },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError((data as { error?: string }).error || `Request failed (${res.status})`, res.status);
  return data as T;
}

export const api = {
  getProfile: (username: string, token: string | null, period = 'overall') =>
    request<{ stats?: Record<string, unknown> }>(`/api/u/${encodeURIComponent(username)}?period=${period}`, token),
  getLeaderboard: (token: string | null) =>
    request<{ leaderboard: unknown[] }>(`/api/leaderboard`, token),
  checkAdmin: (token: string | null) =>
    request<{ role?: string }>(`/api/admin/check`, token),
  getAdminStats: (token: string | null) =>
    request<Record<string, unknown>>(`/api/admin/stats`, token),
  getFriends: (token: string | null) =>
    request<{ friends: unknown[] }>(`/api/friends`, token),
  friendAction: (token: string | null, body: Record<string, unknown>) =>
    request<{ success?: boolean; error?: string }>(`/api/friends`, token, { method: 'POST', body: JSON.stringify(body) }),
  getMessages: (token: string | null, friendId: string) =>
    request<{ messages: unknown[] }>(`/api/messages/${encodeURIComponent(friendId)}`, token),
  sendMessage: (token: string | null, friendId: string, content: string) =>
    request<{ success?: boolean; message?: unknown }>(`/api/messages/${encodeURIComponent(friendId)}`, token, {
      method: 'POST',
      body: JSON.stringify({ content }),
    }),
  spotifyNowPlaying: (token: string | null) =>
    request<Record<string, unknown>>(`/api/spotify/now-playing`, token),
  spotifyControl: (token: string | null, action: string) =>
    request(`/api/spotify/control`, token, { method: 'POST', body: JSON.stringify({ action }) }),
  spotifyLike: (token: string | null, id: string, action: 'like' | 'unlike') =>
    request(`/api/spotify/like`, token, { method: 'POST', body: JSON.stringify({ id, action }) }),
  getSettings: (token: string | null) =>
    request<{
      fmMode?: string;
      showFeatures?: boolean;
      privateMode?: boolean;
      dataSource?: string;
      timezone?: string;
      showTrackPlaycount?: boolean;
      displayName?: string;
    }>(`/api/settings`, token),
  saveSettings: (token: string | null, body: Record<string, unknown>) =>
    request(`/api/settings`, token, { method: 'POST', body: JSON.stringify(body) }),
  spotifyStatus: (token: string | null) =>
    request<{ linked?: boolean }>(`/api/spotify/status`, token),
  spotifyDisconnect: (token: string | null) =>
    request(`/api/spotify/disconnect`, token, { method: 'POST', body: JSON.stringify({}) }),
};
