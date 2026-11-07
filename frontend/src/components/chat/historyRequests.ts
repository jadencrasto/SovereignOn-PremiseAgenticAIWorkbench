export async function loadOwnedHistory<T>(
  list: () => Promise<{ sessions: string[] }>,
  read: (id: string) => Promise<T>,
  isCurrent: () => boolean,
): Promise<T[] | null> {
  const { sessions } = await list();
  if (!isCurrent()) return null;
  const records = await Promise.all(sessions.map(read));
  return isCurrent() ? records : null;
}

export async function deleteOwnedHistory(
  id: string, remove: (id: string) => Promise<unknown>, isCurrent: () => boolean,
): Promise<boolean> {
  await remove(id);
  return isCurrent();
}
