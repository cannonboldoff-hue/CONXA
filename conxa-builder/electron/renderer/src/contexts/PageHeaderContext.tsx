import { createContext, useContext, useMemo, useState, type ReactNode } from 'react'

type PageHeaderState = { title: string; description?: ReactNode; extra?: ReactNode; actions?: ReactNode; onBack?: () => void; backLabel?: string } | null

type PageHeaderContextValue = {
  header: PageHeaderState
  setHeader: (header: PageHeaderState) => void
}

const PageHeaderContext = createContext<PageHeaderContextValue | null>(null)

/** Holds the current page's title/description so AppChrome can render it in
 * the sticky top bar next to the user/logout widget, instead of each page
 * repeating its own header block in the body. Scoped to the routed content
 * so it resets between navigations. */
export function PageHeaderProvider({ children }: { children: ReactNode }) {
  const [header, setHeader] = useState<PageHeaderState>(null)
  const value = useMemo(() => ({ header, setHeader }), [header])
  return <PageHeaderContext.Provider value={value}>{children}</PageHeaderContext.Provider>
}

export function usePageHeaderContext() {
  const ctx = useContext(PageHeaderContext)
  if (!ctx) throw new Error('usePageHeaderContext must be used within PageHeaderProvider')
  return ctx
}
