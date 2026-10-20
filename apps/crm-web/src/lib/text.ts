// Display text for a JSON value from the API: strings and numbers as they are, objects as JSON, never "[object Object]".
export const asText = (v: unknown, fallback = ''): string =>
  typeof v === 'string' ? v
    : typeof v === 'number' || typeof v === 'boolean' || typeof v === 'bigint' ? String(v)
      : v === null || v === undefined ? fallback : JSON.stringify(v);
