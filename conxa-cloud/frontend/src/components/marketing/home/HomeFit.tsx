import { H2, WRAP } from './ui'

const GRID = 'm-0 grid list-none grid-cols-[repeat(auto-fit,minmax(min(180px,100%),1fr))] gap-3 p-0'
const TILE = 'flex flex-col items-center gap-2 rounded-[14px] p-[18px] text-center'

function Icon({ color, children }: { color: string; children: React.ReactNode }) {
  return (
    <svg aria-hidden="true" width="40" height="40" viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="1.5">
      {children}
    </svg>
  )
}

export function HomeFit() {
  return (
    <section id="fit" aria-labelledby="fit-title" className={`${WRAP} py-14`}>
      <h2 id="fit-title" className={H2}>
        Will it fit?
      </h2>
      <ul aria-label="A good fit" className={`${GRID} mt-6`}>
        <li className={`${TILE} border-[1.5px] border-[#4ade80]`}>
          <Icon color="#4ade80">
            <rect x="3" y="4" width="18" height="16" rx="2" />
            <path d="M3 9h18" />
          </Icon>
          Web apps
        </li>
        <li className={`${TILE} border-[1.5px] border-[#4ade80]`}>
          <Icon color="#4ade80">
            <path d="M4 12a8 8 0 0 1 13.7-5.6L20 9M20 4v5h-5M20 12a8 8 0 0 1-13.7 5.6L4 15M4 20v-5h5" />
          </Icon>
          Repeats daily or weekly
        </li>
        <li className={`${TILE} border-[1.5px] border-[#4ade80]`}>
          <Icon color="#4ade80">
            <path d="M9 6h11M9 12h11M9 18h11M4 6h1M4 12h1M4 18h1" />
          </Icon>
          Forms, lists, files
        </li>
        <li className={`${TILE} border-[1.5px] border-[#4ade80]`}>
          <Icon color="#4ade80">
            <path d="M3 5.5 10.5 4.5v7H3zM12 4.3 21 3v8.5h-9zM3 13h7.5v7L3 18.5zM12 13h9v8l-9-1.3z" />
          </Icon>
          Windows computers
        </li>
      </ul>
      <ul aria-label="Not a fit today" className={`${GRID} mt-3 text-[#9ba3af]`}>
        <li className={`${TILE} border-[1.5px] border-dashed border-[#f87171]/60`}>
          <Icon color="#f87171">
            <rect x="3" y="4" width="18" height="12" rx="1.5" />
            <path d="M8 20h8M12 16v4M8 8l8 4M16 8l-8 4" />
          </Icon>
          No desktop apps
        </li>
        <li className={`${TILE} border-[1.5px] border-dashed border-[#f87171]/60`}>
          <Icon color="#f87171">
            <rect x="3" y="4" width="18" height="12" rx="1.5" />
            <path d="M8 20h8M12 16v4M3 3l18 18" />
          </Icon>
          No Mac or Linux
        </li>
        <li className={`${TILE} border-[1.5px] border-dashed border-[#f87171]/60`}>
          <Icon color="#f87171">
            <path d="M8 11V6.5a1.5 1.5 0 0 1 3 0V10V5a1.5 1.5 0 0 1 3 0v5V6a1.5 1.5 0 0 1 3 0v7c0 4-2.5 7-6 7-3 0-4.5-1.5-7-6l1.5-1L8 15zM3 3l18 18" />
          </Icon>
          No drag and drop or canvas
        </li>
        <li className={`${TILE} border-[1.5px] border-dashed border-[#f87171]/60`}>
          <Icon color="#f87171">
            <rect x="3" y="7" width="18" height="10" rx="2" />
            <path d="M7 12h.5M11.5 12h.5M16 12h.5M3 3l18 18" />
          </Icon>
          No unattended CAPTCHAs or fresh codes
        </li>
      </ul>
    </section>
  )
}
