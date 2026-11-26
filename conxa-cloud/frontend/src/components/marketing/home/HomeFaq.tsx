import { H2, WRAP } from './ui'

const FAQ = [
  ['Do I need to write code?', 'No. You do the task and answer questions. Technical users can open and edit any step.'],
  [
    'How is this different from my AI using the browser?',
    'A browsing agent works out each click while it runs. Conxa runs the steps you recorded, and only asks the AI when something moved.',
  ],
  [
    'Which AI assistants work?',
    'Assistants that plug in tools on your computer through a standard called MCP, such as Claude Desktop. The installer also sets up others, including Cursor and VS Code. Without one, fill in a form in the Conxa Execute app (you sign in to Conxa).',
  ],
  [
    'Can it do the wrong thing?',
    "It is built to stop rather than guess, and to check important results. No automation is perfect, so test first and preview anything you can't undo.",
  ],
  [
    'What leaves my computer?',
    'While you build: a copy of the recording video (password boxes blurred) goes through Conxa to a video-analysis service, and page details to AI models. While it runs: whether steps worked, plus computer and user name. Saved sign-ins never leave.',
  ],
  [
    'Two-factor codes and CAPTCHAs?',
    'You sign in yourself, so two-factor at sign-in works. A code or CAPTCHA mid-task needs a pause for a person.',
  ],
  [
    'Why does my computer warn me on install?',
    'Our installers are not code-signed today, so you may see an unknown-publisher warning.',
  ],
  ['SOC 2, ISO 27001, single sign-on?', 'No certifications today, and no SAML single sign-on for the Conxa dashboard.'],
]

export function HomeFaq() {
  return (
    <section id="faq" aria-labelledby="faq-title" className={`${WRAP} py-12`}>
      <h2 id="faq-title" className={H2}>
        Questions.
      </h2>
      <div className="mt-5 grid grid-cols-[repeat(auto-fit,minmax(min(480px,100%),1fr))] items-start gap-x-10">
        {FAQ.map(([q, a]) => (
          <details key={q} className="border-t border-white/12">
            <summary className="cursor-pointer py-3.5 text-[17px] font-semibold">{q}</summary>
            <p className="m-0 mb-4 text-[#9ba3af]">{a}</p>
          </details>
        ))}
      </div>
    </section>
  )
}
