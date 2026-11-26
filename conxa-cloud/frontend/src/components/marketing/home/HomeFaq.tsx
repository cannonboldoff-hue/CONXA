import { H2, WRAP } from './ui'

const FAQ = [
  [
    'Do I need to write code?',
    'No. You do the task once and answer a few questions. If you are technical, you can open and change any step.',
  ],
  [
    'How is this different from asking my AI to use the browser?',
    'An AI using the browser on its own works out every click as it goes, so each time can turn out differently. Conxa repeats the steps you recorded, and only asks the AI for help when something on the page has moved.',
  ],
  [
    'Which AI assistants work with it?',
    'Assistants that can use tools on your computer, such as Claude Desktop. They connect through a standard called MCP. The installer also connects Conxa to others, such as Cursor and VS Code. No assistant? Fill in a simple form in the Conxa Execute app instead (you sign in to Conxa).',
  ],
  [
    'Can it do the wrong thing?',
    "It is built to stop rather than guess, and to check that important steps really worked. No tool is perfect, so try it out first. For anything that can't be undone, ask your AI for a preview: it does every step except that one.",
  ],
  [
    'What leaves my computer?',
    'While you set it up: a copy of your recording (with password boxes blurred) goes through Conxa to an AI service that studies the video, and details of each page go to AI models. While it runs: whether each step worked, plus your computer name and user name. Your saved logins never leave.',
  ],
  [
    'What about one-time codes and "I\'m not a robot" checks?',
    'You log in yourself, so a code at login is fine. If a code or a robot check pops up in the middle of a task, a person has to step in.',
  ],
  [
    'Why does my computer warn me when I install it?',
    "Our installer isn't digitally signed yet, so your computer may warn that the publisher is unknown.",
  ],
  [
    'Do you have security certifications or company sign-in?',
    'Not yet. We have no certifications such as SOC 2 or ISO 27001 today, and you cannot sign in to the Conxa dashboard with your company login (SAML single sign-on).',
  ],
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
