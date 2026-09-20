import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
const DirtyContext = createContext({
  dirty: false,
  setDirty: (_id: string, _dirty: boolean) => {},
});
export const useDirty = () => useContext(DirtyContext);
export function DirtyProvider({ children }: { children: ReactNode }) {
  const [forms, setForms] = useState<Set<string>>(() => new Set());
  const setDirty = useCallback(
    (id: string, dirty: boolean) =>
      setForms((previous) => {
        if (previous.has(id) === dirty) return previous;
        const next = new Set(previous);
        if (dirty) next.add(id);
        else next.delete(id);
        return next;
      }),
    [],
  );
  const dirty = forms.size > 0;
  useEffect(() => {
    const leave = (e: BeforeUnloadEvent) => {
      if (dirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", leave);
    return () => window.removeEventListener("beforeunload", leave);
  }, [dirty]);
  const value = useMemo(() => ({ dirty, setDirty }), [dirty, setDirty]);
  return (
    <DirtyContext.Provider value={value}>{children}</DirtyContext.Provider>
  );
}
