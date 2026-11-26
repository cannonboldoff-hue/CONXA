import { SUPPORT_EMAIL } from '@/lib/contact'

/** Class strings and links shared by the homepage sections. */
export const WRAP = 'mx-auto w-full max-w-[1200px] px-6'
export const H2 = 'm-0 text-[clamp(2rem,3.6vw,3rem)] font-semibold leading-[1.1] tracking-[-0.03em]'

/** "Book a walkthrough" opens the visitor's email app with a message ready to send. */
const BOOKING_SUBJECT = 'Walkthrough request'
const BOOKING_BODY = `Hi,

I would like a walkthrough of Conxa.

The task I repeat is:
My name and company:
`
export const BOOKING_HREF = `mailto:${SUPPORT_EMAIL}?subject=${encodeURIComponent(BOOKING_SUBJECT)}&body=${encodeURIComponent(BOOKING_BODY)}`

const BTN = 'inline-flex items-center justify-center rounded-[10px] font-semibold no-underline'
const SIZE = { sm: 'min-h-11 px-4', md: 'min-h-12 px-[22px]', lg: 'min-h-[52px] px-[26px]' }
type Size = keyof typeof SIZE
export const primaryBtn = (size: Size = 'md') =>
  `${BTN} ${SIZE[size]} bg-linear-to-br from-cyan-400 to-teal-300 text-[#06080b]`
export const ghostBtn = (size: Size = 'md') =>
  `${BTN} ${SIZE[size]} border border-white/40 font-normal text-[#f4f5f7]`

/** A toggle pill (used by the use-case and data tabs). */
export const pill = (on: boolean) =>
  `min-h-11 cursor-pointer rounded-full border px-[18px] text-[15px] font-medium ${
    on ? 'border-[#f4f5f7] bg-[#f4f5f7] text-[#06080b]' : 'border-white/40 bg-transparent text-[#f4f5f7]'
  }`

/** A tinted band: the design alternates plain and raised sections. */
export const BAND = 'border-t border-white/8 bg-[#0b0f14]'
