import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';
export const cn = (...v: ClassValue[]) => twMerge(clsx(v));
export const pct = (v: number | null | undefined, digits = 1) => (v === null || v === undefined ? '—' : `${(v * 100).toFixed(digits)}%`);
export const num = (v: number | null | undefined) => (v === null || v === undefined ? '—' : v.toLocaleString('en-US'));
