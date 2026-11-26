// Conxa homepage film: real Build Studio screens with 2.5D moves, then the run and the stop.
// Silent on purpose: it autoplays muted on the web. Deterministic: frame-driven only.
import { AbsoluteFill, Easing, Img, Sequence, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig } from 'remotion';
import { loadFont } from '@remotion/google-fonts/Geist';

const { fontFamily } = loadFont('normal', { weights: ['400', '500', '600', '700'], subsets: ['latin'] });

const C = { bg: '#06080b', panel: '#0b0f14', line: 'rgba(255,255,255,0.14)', text: '#f4f5f7', muted: '#9ba3af', cyan: '#22d3ee', green: '#4ade80', amber: '#fbbf24' };
const SCENES = [180, 150, 150, 165, 135];
export const TOUR_FRAMES = SCENES.reduce((a, b) => a + b, 0);

const ease = Easing.bezier(0.2, 0.8, 0.2, 1);
const at = (frame, from, to, a, b) =>
  interpolate(frame, [from, to], [a, b], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: ease });

const Fade = ({ duration, children }) => {
  const frame = useCurrentFrame();
  const opacity = Math.min(at(frame, 0, 10, 0, 1), at(frame, duration - 8, duration, 1, 0));
  return <AbsoluteFill style={{ opacity }}>{children}</AbsoluteFill>;
};

const Caption = ({ step, text, note }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame, fps, config: { damping: 200 } });
  return (
    <div style={{ position: 'absolute', left: 64, top: 36, display: 'flex', alignItems: 'center', gap: 18, opacity: s, transform: `translateY(${(1 - s) * 16}px)` }}>
      <div style={{ width: 56, height: 56, borderRadius: 28, background: C.cyan, color: C.bg, fontSize: 30, fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{step}</div>
      <div style={{ fontSize: 44, fontWeight: 600, letterSpacing: -1 }}>{text}</div>
      {note ? <div style={{ fontSize: 22, color: C.muted, marginLeft: 10 }}>{note}</div> : null}
    </div>
  );
};

const Shot = ({ src, width, height, style, children }) => (
  <div style={{ position: 'absolute', width, height, borderRadius: 16, overflow: 'visible', boxShadow: '0 40px 90px rgba(0,0,0,0.55)', ...style }}>
    <Img src={staticFile(src)} style={{ width, height, borderRadius: 16, border: `1px solid ${C.line}` }} />
    {children}
  </div>
);

const Ring = ({ x, y, w, h, from, color = C.cyan }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame: frame - from, fps, config: { damping: 14, mass: 0.6 } });
  if (frame < from) return null;
  return <div style={{ position: 'absolute', left: x, top: y, width: w, height: h, border: `4px solid ${color}`, borderRadius: 12, opacity: s, transform: `scale(${1.12 - 0.12 * s})` }} />;
};

const Cursor = ({ x, y }) => (
  <svg width="34" height="34" viewBox="0 0 22 22" style={{ position: 'absolute', left: x, top: y, filter: 'drop-shadow(0 4px 6px rgba(0,0,0,0.5))' }}>
    <path d="M3 2v16l4.4-4.2 2.9 6.6 2.8-1.2-2.9-6.5H16z" fill="#fff" stroke="#111827" strokeWidth="1.4" />
  </svg>
);

const Check = ({ size = 30, color = C.green, width = 3 }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke={color} strokeWidth={width} strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 8.5 6.5 12 13 4.5" />
  </svg>
);

const Record = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const land = spring({ frame, fps, config: { damping: 200 }, durationInFrames: 50 });
  const W = 880, H = Math.round((W * 2125) / 3360);
  const rows = [435, 507, 578, 650, 781, 852].map((y) => y / 1265);
  const step = Math.min(rows.length - 1, Math.max(0, Math.floor((frame - 40) / 20)));
  const barY = at(frame, 40 + step * 20 - 6, 40 + step * 20, step === 0 ? rows[0] : rows[step - 1], rows[step]);
  return (
    <>
      <Caption step="1" text="Do it once. Conxa writes the steps." />
      <div style={{ position: 'absolute', left: 200, top: 130, width: W, height: H, perspective: 1600 }}>
        <div style={{ position: 'absolute', inset: 0, transformOrigin: '50% 0%', transform: `rotateY(${-14 * (1 - land)}deg) rotateX(${8 * (1 - land)}deg) scale(${0.9 + 0.1 * land})`, opacity: land }}>
          <Shot src="shot-steps.png" width={W} height={H} style={{ left: 0, top: 0 }}>
            {frame > 36 ? <div style={{ position: 'absolute', left: 0.03 * W, width: 0.62 * W, top: (barY - 0.026) * H, height: 0.052 * H, borderRadius: 8, background: 'rgba(34,211,238,0.22)', border: `2px solid ${C.cyan}` }} /> : null}
          </Shot>
        </div>
      </div>
      <div style={{ position: 'absolute', left: 1130, top: 190, width: 130, fontSize: 24, color: C.muted, lineHeight: 1.3 }}>Real screen, practice data</div>
    </>
  );
};

const Answer = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const fly = spring({ frame, fps, config: { damping: 18, mass: 0.8 } });
  const W = 1000, H = Math.round((W * 1975) / 3360);
  const cx = at(frame, 60, 100, 880, 0.053 * W);
  const cy = at(frame, 60, 100, 520, 0.482 * H);
  return (
    <>
      <Caption step="2" text="Answer a few questions" />
      <div style={{ position: 'absolute', left: 140, top: 150, width: W, height: H, transform: `translateX(${(1 - fly) * 520}px)` }}>
        <Shot src="shot-question.png" width={W} height={H} style={{ left: 0, top: 0 }}>
          <Ring x={0.025 * W} y={0.435 * H} w={0.95 * W} h={0.133 * H} from={30} />
          {frame > 106 ? <div style={{ position: 'absolute', left: 0.053 * W - 9, top: 0.482 * H - 9, width: 18, height: 18, borderRadius: 9, background: C.cyan, transform: `scale(${at(frame, 106, 116, 0.2, 1)})` }} /> : null}
          {frame > 60 ? <Cursor x={cx} y={cy} /> : null}
          {frame > 104 ? <div style={{ position: 'absolute', left: 0.053 * W - 24, top: 0.482 * H - 24, width: 48, height: 48, borderRadius: 24, border: `3px solid ${C.cyan}`, opacity: at(frame, 104, 120, 1, 0), transform: `scale(${at(frame, 104, 120, 0.5, 1.7)})` }} /> : null}
        </Shot>
      </div>
    </>
  );
};

const Test = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const k = 1.15;
  const rise = spring({ frame, fps, config: { damping: 200 } });
  return (
    <>
      <Caption step="3" text="Test it with your own list" />
      <div style={{ position: 'absolute', left: 157, top: 150, width: 840 * k, height: 445 * k, transform: `translateY(${(1 - rise) * 60}px)`, opacity: rise }}>
        <Shot src="shot-test.png" width={840 * k} height={445 * k} style={{ left: 0, top: 0 }}>
          {[139, 164, 189].map((y, i) => {
            const from = 30 + i * 24;
            const w = at(frame, from, from + 18, 0, 760);
            return (
              <div key={y}>
                <div style={{ position: 'absolute', left: 30 * k, top: (y - 12) * k, width: w * k, height: 24 * k, borderRadius: 6, background: 'rgba(34,211,238,0.18)' }} />
                {frame > from + 18 ? <div style={{ position: 'absolute', left: 770 * k, top: (y - 15) * k }}><Check /></div> : null}
              </div>
            );
          })}
          <Ring x={18 * k} y={385 * k} w={93 * k} h={40 * k} from={104} />
        </Shot>
      </div>
    </>
  );
};

const STEPS = ['Open', 'Fill', 'Attach', 'Save', 'Check'];
const Run = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const pop = (from) => spring({ frame: frame - from, fps, config: { damping: 14, mass: 0.6 } });
  const done = [35, 50, 65, 105, 120];
  return (
    <>
      <Caption step="4" text="Your AI runs the same steps" />
      <div style={{ position: 'absolute', right: 120, top: 150, padding: '18px 26px', borderRadius: 26, background: '#2c2c2c', fontSize: 30, opacity: pop(8), transform: `scale(${0.8 + 0.2 * pop(8)})`, transformOrigin: 'right top' }}>
        Enter the Northline bill
      </div>
      <div style={{ position: 'absolute', left: 110, right: 110, top: 360, height: 6, borderRadius: 3, background: '#17202c' }}>
        <div style={{ width: `${at(frame, 25, 120, 0, 100)}%`, height: 6, borderRadius: 3, background: C.cyan }} />
      </div>
      {STEPS.map((label, i) => {
        const x = 110 + (1060 / 4) * i;
        const moved = i === 3 && frame >= 78 && frame < done[3];
        const ok = frame >= done[i];
        const shake = moved ? Math.sin(frame * 1.7) * 5 : 0;
        return (
          <div key={label} style={{ position: 'absolute', left: x - 80, top: 322, width: 160, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 16, transform: `translateX(${shake}px)` }}>
            <div style={{ width: 82, height: 82, borderRadius: 41, display: 'flex', alignItems: 'center', justifyContent: 'center', border: `5px solid ${ok ? C.green : moved ? C.amber : '#334155'}`, background: ok ? C.green : C.bg, fontSize: 30, fontWeight: 700, color: moved ? C.amber : C.muted, transform: `scale(${ok ? 0.85 + 0.15 * pop(done[i]) : 1})` }}>
              {ok ? <Check size={46} color={C.bg} width={2.8} /> : i + 1}
            </div>
            <div style={{ fontSize: 30, fontWeight: 600 }}>{label}</div>
          </div>
        );
      })}
      {frame >= 78 ? (
        <div style={{ position: 'absolute', left: 110 + (1060 / 4) * 3 - 190, top: 500, width: 380, textAlign: 'center', fontSize: 26, color: frame < 105 ? C.amber : C.green, opacity: pop(78) }}>
          {frame < 105 ? 'Save button moved…' : 'Found it by its name'}
        </div>
      ) : null}
      <div style={{ position: 'absolute', left: 390, top: 580, width: 500, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 14, padding: '20px 0', borderRadius: 18, background: '#163226', fontSize: 32, fontWeight: 600, opacity: pop(132), transform: `translateY(${(1 - pop(132)) * 24}px)` }}>
        <Check size={34} />
        Done. Checked in the list.
      </div>
    </>
  );
};

const Stop = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const W = 1100, s = W / 660, H = Math.round((W * 1050) / 3300);
  const inS = spring({ frame, fps, config: { damping: 200 } });
  const big = spring({ frame: frame - 28, fps, config: { damping: 16 } });
  const end = at(frame, 84, 100, 0, 1);
  const mark = (x, y, w, from) => (
    <div style={{ position: 'absolute', left: (x - 3) * s, top: (y + 1) * s, width: at(frame, from, from + 16, 0, w + 6) * s, height: 20 * s, borderRadius: 6, background: 'rgba(34,211,238,0.30)' }} />
  );
  return (
    <>
      <div style={{ opacity: 1 - end }}>
        <div style={{ position: 'absolute', left: 90, top: 70, width: W, height: H, opacity: inS, transform: `translateY(${(1 - inS) * 40}px)` }}>
          <Shot src="shot-why-failed.png" width={W} height={H} style={{ left: 0, top: 0 }}>
            {mark(355, 148, 195, 24)}
            {mark(45, 172, 133, 44)}
          </Shot>
        </div>
        <div style={{ position: 'absolute', left: 90, top: 470, width: 1100, opacity: big, transform: `translateY(${(1 - big) * 30}px)` }}>
          <div style={{ fontSize: 84, fontWeight: 700, letterSpacing: -3, lineHeight: 1.05 }}>Not sure? It stops.</div>
          <div style={{ marginTop: 18, fontSize: 28, color: C.muted }}>A real message from the app</div>
        </div>
      </div>
      <AbsoluteFill style={{ opacity: end, alignItems: 'center', justifyContent: 'center', gap: 34 }}>
        <Img src={staticFile('conxa-icon.png')} style={{ width: 120, height: 120, borderRadius: 28 }} />
        <div style={{ fontSize: 64, fontWeight: 600, letterSpacing: -2, textAlign: 'center' }}>Record once. Your AI repeats it.</div>
      </AbsoluteFill>
    </>
  );
};

export const Tour = () => {
  const parts = [Record, Answer, Test, Run, Stop];
  let from = 0;
  return (
    <AbsoluteFill style={{ background: C.bg, color: C.text, fontFamily }}>
      {parts.map((Part, i) => {
        const start = from;
        from += SCENES[i];
        const last = i === parts.length - 1;
        return (
          <Sequence key={i} from={start} durationInFrames={SCENES[i]}>
            {last ? (
              <AbsoluteFill><Part /></AbsoluteFill>
            ) : (
              <Fade duration={SCENES[i]}><Part /></Fade>
            )}
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
