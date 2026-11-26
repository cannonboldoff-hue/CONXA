import { H2, WRAP } from './ui'

type V = 'y' | 'p' | 'n'
type Cell = V | [V, string]

const LABEL: Record<V, string> = { y: 'Yes', p: 'Partly', n: 'No' }

/** Columns after Conxa: RPA, Scripts, AI agents, Integrations. */
const ROWS: { q: string; cells: Cell[] }[] = [
  { q: 'Set it up without code', cells: ['y', 'p', 'n', 'y', 'p'] },
  { q: 'Skip needing an API', cells: ['y', 'y', 'y', 'y', 'n'] },
  { q: 'Get the same steps every run', cells: ['y', 'y', 'y', 'n', 'y'] },
  {
    q: 'Keep running when screens change',
    cells: ['y', ['p', 'Partly, some offer AI repair'], 'n', 'y', ['y', 'Yes, not affected by screens']],
  },
  { q: 'Run without AI clicking', cells: ['y', 'y', 'y', 'n', 'y'] },
  {
    q: 'Do the runs on your own computer',
    cells: ['y', ['p', 'Partly, some bots run on servers'], 'y', ['p', 'Partly, the AI itself runs in the cloud'], 'n'],
  },
  {
    q: 'Cost stays flat as runs grow',
    cells: [
      ['y', 'Yes, no charge per run'],
      ['p', 'Partly, licence per bot'],
      'y',
      ['n', 'No, tokens every run'],
      ['p', 'Partly, some charge per task'],
    ],
  },
  {
    q: 'Hand it to other computers',
    cells: [
      'y',
      ['p', 'Partly, through the platform'],
      ['p', 'Partly, do it yourself'],
      ['p', 'Partly, share a prompt or shortcut'],
      ['p', 'Partly, each person connects'],
    ],
  },
]
const HEADS = ['RPA', 'Scripts', 'AI agents', 'Integrations']

function Mark({ cell, onDark }: { cell: Cell; onDark?: boolean }) {
  const [v, label] = typeof cell === 'string' ? [cell, LABEL[cell]] : cell
  const common = { role: 'img', 'aria-label': label ?? LABEL[v], width: 26, height: 26, viewBox: '0 0 24 24', className: 'mx-auto' } as const
  if (v === 'y')
    return (
      <svg {...common}>
        <circle cx="12" cy="12" r="10" fill={onDark ? '#4ade80' : '#06080b'} />
        <path d="M7.5 12.5 10.5 15.5 16.5 9" fill="none" stroke={onDark ? '#06080b' : '#fff'} strokeWidth="2.4" />
      </svg>
    )
  if (v === 'p')
    return (
      <svg {...common}>
        <circle cx="12" cy="12" r="9" fill="none" stroke="#475569" strokeWidth="2" />
        <path d="M12 3a9 9 0 0 1 0 18z" fill="#475569" />
      </svg>
    )
  return (
    <svg {...common}>
      <path d="M7 12h10" stroke="#64748b" strokeWidth="2.6" />
    </svg>
  )
}

export function HomeCompare() {
  return (
    <section id="compare" aria-labelledby="compare-title" className="bg-[#f4f5f7] text-[#06080b]">
      <div className={`${WRAP} py-16`}>
        <h2 id="compare-title" className={H2}>
          Same route, every run.
        </h2>
    <div style={{ marginTop: "24px", display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(460px, 100%), 1fr))", gap: "20px" }}>
      <figure style={{ margin: "0", padding: "18px", borderRadius: "16px", background: "#ffffff", border: "1px solid #d6dae0" }}>
        <figcaption style={{ fontWeight: "600" }}>AI browsing agent: works out the route each time</figcaption>
        <svg role="img" aria-label="Three runs of a browsing agent take three different routes to the goal" viewBox="0 0 520 170" style={{ display: "block", width: "100%", height: "auto", marginTop: "10px" }}>
          <circle cx="40" cy="85" r="12" fill="#06080b"></circle>
          <circle cx="480" cy="85" r="14" fill="none" stroke="#06080b" strokeWidth="3"></circle><circle cx="480" cy="85" r="5" fill="#06080b"></circle>
          <path d="M52 85 C 120 10, 180 20, 230 70 S 340 140, 466 85" fill="none" stroke="#7c3aed" strokeWidth="3.5" strokeLinecap="round" pathLength="100" strokeDasharray="100" style={{ animation: "hx-draw1 8s ease-in-out infinite" }}></path>
          <path d="M52 85 C 90 160, 170 170, 220 110 S 300 40, 360 120 S 440 120, 466 90" fill="none" stroke="#ea580c" strokeWidth="3.5" strokeLinecap="round" pathLength="100" strokeDasharray="100" style={{ animation: "hx-draw2 8s ease-in-out infinite" }}></path>
          <path d="M52 85 C 140 90, 150 40, 270 30 S 330 100, 400 60 S 450 70, 466 82" fill="none" stroke="#0284c7" strokeWidth="3.5" strokeLinecap="round" pathLength="100" strokeDasharray="100" style={{ animation: "hx-draw3 8s ease-in-out infinite" }}></path>
        </svg>
      </figure>
      <figure style={{ margin: "0", padding: "18px", borderRadius: "16px", background: "#06080b", color: "#f4f5f7" }}>
        <figcaption style={{ display: "flex", flexWrap: "wrap", alignItems: "center", justifyContent: "space-between", gap: "8px", fontWeight: "600" }}>Conxa: follows the steps you recorded<span style={{ display: "inline-flex", alignItems: "center", gap: "8px", padding: "6px 12px", borderRadius: "999px", background: "#163226", color: "#bbf7d0", fontSize: "14px", fontWeight: "500" }}><svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#4ade80" strokeWidth="1.8"><rect x="4" y="5" width="16" height="11" rx="1.5"></rect><path d="M2 19h20"></path></svg>Runs on your computer</span></figcaption>
        <svg role="img" aria-label="Three runs of Conxa follow the same straight route through the same steps" viewBox="0 0 520 170" style={{ display: "block", width: "100%", height: "auto", marginTop: "10px" }}>
          <path d="M40 85H480" stroke="#22d3ee" strokeWidth="4"></path>
          <circle cx="40" cy="85" r="12" fill="#f4f5f7"></circle>
          <circle cx="150" cy="85" r="9" fill="#06080b" stroke="#22d3ee" strokeWidth="4"></circle>
          <circle cx="260" cy="85" r="9" fill="#06080b" stroke="#22d3ee" strokeWidth="4"></circle>
          <circle cx="370" cy="85" r="9" fill="#06080b" stroke="#22d3ee" strokeWidth="4"></circle>
          <circle cx="480" cy="85" r="14" fill="none" stroke="#f4f5f7" strokeWidth="3"></circle><circle cx="480" cy="85" r="5" fill="#f4f5f7"></circle>
          <circle cx="48" cy="85" r="11" fill="#4ade80" style={{ animation: "hx-runner 8s linear infinite" }}></circle>
          <text x="150" y="125" fill="#c9ced6" fontSize="15" textAnchor="middle">Open</text>
          <text x="260" y="125" fill="#c9ced6" fontSize="15" textAnchor="middle">Fill</text>
          <text x="370" y="125" fill="#c9ced6" fontSize="15" textAnchor="middle">Save</text>
        </svg>
      </figure>
    </div>

        <div className="mt-7 overflow-x-auto">
          <table aria-labelledby="compare-title" className="w-full min-w-[760px] border-collapse text-center text-[15px]">
            <thead>
              <tr>
                <th scope="col" className="w-[28%] border-b-2 border-[#06080b] p-3 text-left">
                  <span className="font-medium text-[#3a4048]">Can you…</span>
                </th>
                <th scope="col" className="rounded-t-xl bg-[#06080b] p-3 text-[#f4f5f7]">
                  Conxa
                </th>
                {HEADS.map((h) => (
                  <th key={h} scope="col" className="border-b-2 border-[#06080b] p-3">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {ROWS.map((r, i) => {
                const last = i === ROWS.length - 1
                const rule = last ? '' : 'border-b border-[#d6dae0]'
                return (
                  <tr key={r.q}>
                    <th scope="row" className={`px-3 py-2.5 text-left font-medium ${rule}`}>
                      {r.q}
                    </th>
                    <td className={`bg-[#06080b] p-2.5 ${last ? 'rounded-b-xl' : ''}`}>
                      <Mark cell={r.cells[0]} onDark />
                    </td>
                    {r.cells.slice(1).map((c, j) => (
                      <td key={j} className={rule}>
                        <Mark cell={c} />
                      </td>
                    ))}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  )
}
