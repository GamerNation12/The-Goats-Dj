export interface JwtUser {
  id: string;
  name: string;
  avatar?: string;
  image?: string;
  [k: string]: unknown;
}

export interface RecentTrack {
  name: string;
  artist: string;
  image?: string;
  nowPlaying?: boolean;
  url?: string;
  date?: string | null;
}

export interface TopItem {
  name: string;
  artist?: string;
  image?: string;
  playcount?: number | string;
}

export interface UserStats {
  playcount?: number;
  recentTracks?: RecentTrack[];
  topArtists?: TopItem[];
  topTracks?: TopItem[];
}

export interface LeaderboardEntry {
  username: string;
  avatar?: string;
  total_scrobbles: string | number;
}

export interface Friend {
  friend_id: string;
  friend_username: string;
  display_name?: string;
  status: 'pending' | 'accepted';
  direction?: 'incoming' | 'outgoing';
}

export interface ChatMessage {
  id: string | number;
  sender_id: string;
  content: string;
  sent_at: string;
}

export interface SpotifyNowPlaying {
  id?: string;
  title?: string;
  artist?: string;
  image?: string;
  is_playing?: boolean;
  is_liked?: boolean;
}
