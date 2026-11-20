// The user's order of the subtitle types: a list of ids. A type it does not name comes after the named ones, in the
// order it came in (the server sorts the same way, settings.py `ordered`).

/** `items` in the order `order` gives. */
export function inOrder<T extends { id: string }>(items: T[], order: string[]): T[] {
  const rank = new Map(order.map((id, i) => [id, i]));
  return items
    .map((item, i) => ({ item, i }))
    .sort((a, b) => (rank.get(a.item.id) ?? order.length) - (rank.get(b.item.id) ?? order.length) || a.i - b.i)
    .map((x) => x.item);
}

/** The order of `ids` (as shown) after moving `id` to position `to`. */
export function moved(ids: string[], id: string, to: number): string[] {
  const from = ids.indexOf(id);
  if (from < 0 || to < 0 || to >= ids.length || from === to) return ids;
  const out = ids.filter((x) => x !== id);
  out.splice(to, 0, id);
  return out;
}
