export function LogoMark({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="7" className="logo-bg" />
      <path
        d="M6 22 L11 22 L14 12 L17 26 L20 16 L23 22 L26 22"
        stroke="currentColor"
        strokeWidth="2.4"
        fill="none"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  )
}

export function IconPlay() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M4.5 2.8v10.4c0 .7.75 1.15 1.35.78l8.2-5.2a.93.93 0 0 0 0-1.56l-8.2-5.2A.93.93 0 0 0 4.5 2.8Z" />
    </svg>
  )
}

export function IconCopy() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
      <rect x="5.5" y="5.5" width="8" height="8" rx="1.5" />
      <path d="M10.5 3.5A1.5 1.5 0 0 0 9 2H3.5A1.5 1.5 0 0 0 2 3.5V9a1.5 1.5 0 0 0 1.5 1.5" />
    </svg>
  )
}

export function IconClose() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
      <path d="m3.5 3.5 9 9m0-9-9 9" />
    </svg>
  )
}

export function IconCheck() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
      <path d="m3 8.4 3.4 3.4L13 4.8" />
    </svg>
  )
}

export function IconAlert() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
      <path d="M8 2.5 14.5 13.5h-13L8 2.5Z" strokeLinejoin="round" />
      <path d="M8 6.5v3.2M8 11.6v.01" strokeLinecap="round" />
    </svg>
  )
}

export function IconTrash() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true">
      <path d="M2.5 4.2h11M6.2 2.5h3.6M4 4.2l.7 9.3h6.6l.7-9.3M6.5 7v4M9.5 7v4" strokeLinecap="round" />
    </svg>
  )
}

export function IconSpinner() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" className="spin" aria-hidden="true">
      <circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeDasharray="26 12" strokeLinecap="round" />
    </svg>
  )
}

export function IconHistory() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true">
      <path d="M2.5 8a5.5 5.5 0 1 0 1.6-3.9M2.5 2.8V5h2.2" strokeLinecap="round" />
      <path d="M8 5.2V8l2 1.6" strokeLinecap="round" />
    </svg>
  )
}

export function IconChevron() {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
      <path d="m4 6 4 4 4-4" strokeLinecap="round" />
    </svg>
  )
}
