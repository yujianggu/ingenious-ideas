export class DirtyRegistry {
  private dirty = new Set<string>();
  mark(entity: string, changed: boolean) {
    if (changed) this.dirty.add(entity);
    else this.dirty.delete(entity);
  }
  get hasChanges() {
    return this.dirty.size > 0;
  }
  get entities() {
    return [...this.dirty];
  }
  discard(confirmed: boolean) {
    if (!confirmed) return false;
    this.dirty.clear();
    return true;
  }
}
export function formIdentity(
  episode: { id: string; version: number; revision: number },
  epoch: number,
  entity: string,
) {
  return `${episode.id}:${episode.version}:${epoch}:${entity}`;
}
