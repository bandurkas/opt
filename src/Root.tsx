import React from 'react';
import { Composition } from 'remotion';
import { HeroAnimation } from './components/scenes/HeroAnimation';

export const RemotionRoot: React.FC = () => {
  return (
    <>
      {/* Main Hero Animation - 45 seconds at 30fps */}
      <Composition
        id="HeroAnimation"
        component={HeroAnimation}
        durationInFrames={1350}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={{
          width: 1920,
          height: 1080,
          durationInFrames: 1350,
        }}
      />

      {/* Mobile version - vertical format */}
      <Composition
        id="HeroAnimation_Mobile"
        component={HeroAnimation}
        durationInFrames={1350}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={{
          width: 1080,
          height: 1920,
          durationInFrames: 1350,
        }}
      />

      {/* Preview composition - 15 seconds */}
      <Composition
        id="HeroAnimation_Preview"
        component={HeroAnimation}
        durationInFrames={450}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={{
          width: 1920,
          height: 1080,
          durationInFrames: 450,
        }}
      />
    </>
  );
};
