import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import en from './locales/en'
import id from './locales/id'

export type Lang = 'en' | 'id'

type Dict = Record<string, string>
const dicts: Record<Lang, Dict> = { en, id }

interface I18n {
  lang: Lang
  setLang: (l: Lang) => void
  tr: (key: string, vars?: Record<string, string | number>) => string
}

const Ctx = createContext<I18n>({ lang: 'en', setLang: () => undefined, tr: (k) => k })

export function I18nProvider({ initial, children }: { initial: Lang; children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(initial)
  useEffect(() => {
    document.documentElement.lang = lang
  }, [lang])
  const setLang = useCallback((l: Lang) => setLangState(l), [])
  const tr = useCallback(
    (key: string, vars?: Record<string, string | number>): string => {
      let s = dicts[lang][key] ?? dicts.en[key] ?? key
      if (vars) {
        for (const [k, v] of Object.entries(vars)) s = s.replaceAll(`{${k}}`, String(v))
      }
      return s
    },
    [lang]
  )
  const value = useMemo(() => ({ lang, setLang, tr }), [lang, setLang, tr])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useI18n(): I18n {
  return useContext(Ctx)
}
