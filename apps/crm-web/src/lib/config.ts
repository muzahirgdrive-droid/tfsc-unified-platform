import { useQuery } from '@tanstack/react-query';
import { raw } from './api';

export type ClientConfig = { dialer: { mode: string; template: string | null }; celebration_seconds: number; agreements_url: string | null; business_timezone: string };
export const useConfig = (enabled = true) => useQuery({ queryKey: ['config'], queryFn: () => raw<ClientConfig>('/api/config'), staleTime: 300_000, enabled });
