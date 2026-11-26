import { Composition, registerRoot } from 'remotion';
import { Tour, TOUR_FRAMES } from './Tour';

const Root = () => (
  <Composition id="Tour" component={Tour} durationInFrames={TOUR_FRAMES} fps={30} width={1280} height={720} />
);

registerRoot(Root);
