export async function loadOwnedHistory<T>(
  list: () => Promise<{ sessions: string[] }>,
  read: (id: string) => Promise<T>,
  isCurrent: () => boolean,
): Promise<T[] | null> {
  const { sessions } = await list();
  if (!isCurrent()) return null;
  const settled = await Promise.allSettled([...new Set(sessions)].map(read));
  if (!isCurrent()) return null;
  const records: T[] = [];
  for (const item of settled) {
    if (item.status === 'fulfilled') records.push(item.value);
    else if (item.reason?.status !== 404) throw item.reason;
  }
  return records;
}

export async function deleteOwnedHistory(
  id: string, remove: (id: string) => Promise<unknown>, isCurrent: () => boolean,
): Promise<boolean> {
  await remove(id);
  return isCurrent();
}
