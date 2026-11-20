// One queue for every request that writes the track (autosave, start over): each runs after the previous one
// has finished, so each sends the revision the previous one returned. Two writes in flight at once would let
// the second carry an outdated revision and be refused as "changed elsewhere".

export type Serial = <T>(task: () => Promise<T>) => Promise<T>;

export function serial(): Serial {
  let tail: Promise<unknown> = Promise.resolve();
  return <T>(task: () => Promise<T>): Promise<T> => {
    const run = tail.then(task, task); // a failed task does not block the ones after it
    tail = run.catch(() => undefined);
    return run;
  };
}
