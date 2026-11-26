'use client'

import { useEffect, useRef } from 'react'
import Image from 'next/image'
import { H2, BAND, WRAP } from './ui'
import { useHomePaused } from './HomeMotion'

const THUMB = 'h-[86px] w-full rounded-md object-cover'
const CHAPTER =
  'flex cursor-pointer flex-col gap-2 rounded-xl border border-white/14 bg-[#06080b] p-2 text-left text-[15px] font-semibold text-[#f4f5f7]'

const CHAPTERS = [
  { label: '1. Record', at: 0, shot: 'record', pos: 'left top', w: 1100, h: 545 },
  { label: '2. Answer questions', at: 6, shot: 'question', pos: 'left top', w: 3360, h: 1975 },
  { label: '3. Try it out', at: 11, shot: 'test', pos: 'left top', w: 3360, h: 1775 },
  { label: '4. Your AI does it', at: 16 },
  { label: '5. Stops if unsure', at: 21.5, shot: 'why-failed', pos: 'left center', w: 3300, h: 1050 },
]

export function HomeFilm() {
  const video = useRef<HTMLVideoElement>(null)
  const paused = useHomePaused()

  useEffect(() => {
    const el = video.current
    if (!el) return
    el.muted = true // autoplay needs the muted state set on the element, not just the attribute
    const calm = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (paused || calm) el.pause()
    else el.play().catch(() => {})
  }, [paused])

  const seek = (at: number) => () => {
    const el = video.current
    if (!el) return
    el.currentTime = at
    el.play().catch(() => {})
  }

  return (
    <section id="film" aria-labelledby="film-title" className={BAND}>
      <div className={`${WRAP} py-14`}>
        <h2 id="film-title" className={`${H2} mb-6`}>
          See the real app.
        </h2>
        <video
          ref={video}
          src="/marketing/film/tour.mp4"
          poster="/marketing/film/poster.jpg"
          controls
          autoPlay
          muted
          loop
          playsInline
          preload="metadata"
          aria-label="26-second film of the Conxa app: record a task, answer questions, try it on a short list, have an AI assistant do it, and a real message where it stops instead of guessing"
          className="block aspect-video h-auto w-full rounded-2xl border border-white/14 bg-[#06080b]"
        />
        <div
          role="group"
          aria-label="Jump to a part of the film"
          className="mt-3.5 grid grid-cols-[repeat(auto-fit,minmax(min(200px,100%),1fr))] gap-3"
        >
          {CHAPTERS.map((c) => (
            <button key={c.label} type="button" onClick={seek(c.at)} className={CHAPTER}>
              {c.shot ? (
                <Image
                  src={`/marketing/shots/shot-${c.shot}.png`}
                  alt=""
                  width={c.w}
                  height={c.h}
                  sizes="240px"
                  className={THUMB}
                  style={{ objectPosition: c.pos }}
                />
              ) : (
                <span className={`${THUMB} flex items-center justify-center bg-[#1a1a1a]`}>
                  <svg aria-hidden="true" width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="#22d3ee" strokeWidth="1.6">
                    <path d="M4 5h16v11H9l-5 4zM9 9l2.5 2.5L16 7.5" />
                  </svg>
                </span>
              )}
              {c.label}
            </button>
          ))}
        </div>
        <details className="mt-2.5 text-sm text-[#9ba3af]">
          <summary className="min-h-11 cursor-pointer leading-[44px] text-[#f4f5f7]">The film in words</summary>
          <ol className="m-0 pl-[18px]">
            <li>You do the task once in Conxa&apos;s browser while it records.</li>
            <li>It asks which details change each time.</li>
            <li>You try it out on a short list of three people.</li>
            <li>Your AI does the same five steps. The Save button has moved, and it still finds it.</li>
            <li>When it is not sure, it stops and says why. Nothing is clicked.</li>
          </ol>
        </details>
      </div>
    </section>
  )
}
