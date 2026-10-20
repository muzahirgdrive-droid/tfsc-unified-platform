/** Unsaved Sheet View draft, encrypted at rest in IndexedDB with an AES-GCM key that lives only in
 * this tab's sessionStorage; cleared on logout (§11.2). */
const DB = 'crm-drafts';
const KEY = 'crm.draftkey';

async function key(): Promise<CryptoKey> {
  let raw = sessionStorage.getItem(KEY);
  if (!raw) { raw = btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(32)))); sessionStorage.setItem(KEY, raw); }
  return crypto.subtle.importKey('raw', Uint8Array.from(atob(raw), (c) => c.charCodeAt(0)) as BufferSource, 'AES-GCM', false, ['encrypt', 'decrypt']);
}
function db(): Promise<IDBDatabase> {
  return new Promise((res, rej) => { const r = indexedDB.open(DB, 1); r.onupgradeneeded = () => r.result.createObjectStore('d'); r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); });
}
export async function saveDraft(userId: string, value: unknown): Promise<void> {
  try {
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const data = await crypto.subtle.encrypt({ name: 'AES-GCM', iv: iv as BufferSource }, await key(), new TextEncoder().encode(JSON.stringify(value)) as BufferSource);
    const d = await db(); d.transaction('d', 'readwrite').objectStore('d').put({ iv, data }, userId);
  } catch { /* drafts are a convenience; ignore storage failures */ }
}
export async function loadDraft<T>(userId: string): Promise<T | null> {
  try {
    const d = await db();
    const rec = await new Promise<{ iv: Uint8Array; data: ArrayBuffer } | undefined>((res) => { const r = d.transaction('d').objectStore('d').get(userId); r.onsuccess = () => res(r.result); r.onerror = () => res(undefined); });
    if (!rec) return null;
    const plain = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: rec.iv as BufferSource }, await key(), rec.data);
    return JSON.parse(new TextDecoder().decode(plain)) as T;
  } catch { return null; }
}
export async function clearDraft(userId: string): Promise<void> {
  try { const d = await db(); d.transaction('d', 'readwrite').objectStore('d').delete(userId); } catch { /* ignore */ }
}
