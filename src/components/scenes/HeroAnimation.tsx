import React, { Suspense, useMemo } from 'react';
import { Canvas } from '@react-three/fiber';
import { useCurrentFrame, interpolate } from 'remotion';
import { CameraController, CAMERA_KEYFRAMES } from '../animation/CameraController';
import { LightingSetup } from '../three/LightingSetup';
import { AIWorldGenerator } from '../three/WorldGenerator';
import { ComponentGenerator } from '../three/ComponentGenerator';
import { BRAND } from '../../config/brandKit';

// Scene timing constants (in frames, at 30fps)
const SCENES = {
  SCENE1: { start: 0, end: 150, duration: 150 },      // 0-5s
  SCENE2: { start: 150, end: 300, duration: 150 },    // 5-10s
  SCENE3: { start: 300, end: 450, duration: 150 },    // 10-15s
  SCENE4: { start: 450, end: 600, duration: 150 },    // 15-20s
  SCENE5: { start: 600, end: 750, duration: 150 },    // 20-25s
  SCENE6: { start: 750, end: 960, duration: 210 },    // 25-32s
  SCENE7: { start: 960, end: 1140, duration: 180 },   // 32-38s
  SCENE8: { start: 1140, end: 1350, duration: 210 },  // 38-45s
} as const;

interface HeroAnimationProps {
  width?: number;
  height?: number;
  durationInFrames?: number;
}

export const HeroAnimation: React.FC<HeroAnimationProps> = ({
  width = 1920,
  height = 1080,
  durationInFrames = 1350,
}) => {
  const frame = useCurrentFrame();

  // Determine current scene
  const getCurrentScene = (): keyof typeof SCENES => {
    if (frame < SCENES.SCENE2.start) return 'SCENE1';
    if (frame < SCENES.SCENE3.start) return 'SCENE2';
    if (frame < SCENES.SCENE4.start) return 'SCENE3';
    if (frame < SCENES.SCENE5.start) return 'SCENE4';
    if (frame < SCENES.SCENE6.start) return 'SCENE5';
    if (frame < SCENES.SCENE7.start) return 'SCENE6';
    if (frame < SCENES.SCENE8.start) return 'SCENE7';
    return 'SCENE8';
  };

  const currentScene = getCurrentScene();
  const sceneConfig = SCENES[currentScene];
  const sceneProgress = (frame - sceneConfig.start) / sceneConfig.duration;

  // Get appropriate camera keyframes based on scene
  const getCameraKeyframes = () => {
    const keyframesMap = {
      SCENE1: CAMERA_KEYFRAMES.scene1,
      SCENE2: CAMERA_KEYFRAMES.scene2,
      SCENE3: CAMERA_KEYFRAMES.scene3,
      SCENE4: CAMERA_KEYFRAMES.scene4,
      SCENE5: CAMERA_KEYFRAMES.scene5,
      SCENE6: CAMERA_KEYFRAMES.scene6,
      SCENE7: CAMERA_KEYFRAMES.scene7,
      SCENE8: CAMERA_KEYFRAMES.scene8,
    };
    return keyframesMap[currentScene];
  };

  // Determine component visibility and state
  const shouldShowComponents = frame >= SCENES.SCENE5.start;
  const isExploded = frame >= SCENES.SCENE5.start && frame < SCENES.SCENE6.start;
  const isReassembling = frame >= SCENES.SCENE6.start && frame < SCENES.SCENE7.start;
  const componentProgress = isReassembling
    ? (frame - SCENES.SCENE6.start) / SCENES.SCENE6.duration
    : 0;

  // Background color based on scene
  const getBackgroundColor = () => {
    if (currentScene === 'SCENE8') {
      // CTA scene - cleaner, lighter background
      return '#1a1a1a';
    }
    return '#0D0D0D';
  };

  // Enable effects based on scene
  const enableCameraDrift = ![' SCENE4', 'SCENE8'].includes(currentScene);
  const enableCameraShake = ['SCENE3', 'SCENE5'].includes(currentScene);

  return (
    <div
      style={{
        width: '100%',
        height: '100%',
        background: getBackgroundColor(),
        position: 'relative',
        overflow: 'hidden',
      }}
    >
      <Canvas
        style={{ width: '100%', height: '100%' }}
        gl={{
          antialias: true,
          alpha: true,
          powerPreference: 'high-performance',
          precision: 'highp',
        }}
        camera={{
          position: [0, 0, 100],
          fov: 75,
          near: 0.1,
          far: 1000,
        }}
      >
        <Suspense fallback={null}>
          {/* Lighting */}
          <LightingSetup
            scene={parseInt(currentScene.replace('SCENE', ''))}
          />

          {/* Environment */}
          <color attach="background" args={[getBackgroundColor()]} />

          {/* 3D Scene */}
          <group>
            {/* World (visible in scenes 1-3) */}
            {frame < SCENES.SCENE4.start && (
              <group position={[0, 0, 0]}>
                <AIWorldGenerator />
              </group>
            )}

            {/* Components (visible in scenes 5-8) */}
            {shouldShowComponents && (
              <group position={[0, 0, 0]}>
                <ComponentGenerator
                  isExploded={isExploded}
                  isReassembling={isReassembling}
                  progress={componentProgress}
                />
              </group>
            )}

            {/* Final AI System visualization (scene 7+) */}
            {frame >= SCENES.SCENE7.start && (
              <FinalAISystem progress={(frame - SCENES.SCENE7.start) / SCENES.SCENE7.duration} />
            )}
          </group>

          {/* Camera */}
          <CameraController
            keyframes={getCameraKeyframes()}
            enableDrift={enableCameraDrift}
            enableShake={enableCameraShake}
            shakeIntensity={0.15}
          />
        </Suspense>
      </Canvas>

      {/* UI Overlays */}
      <SceneOverlays currentScene={currentScene} sceneProgress={sceneProgress} />
    </div>
  );
};

interface SceneOverlaysProps {
  currentScene: keyof typeof SCENES;
  sceneProgress: number;
}

const SceneOverlays: React.FC<SceneOverlaysProps> = ({ currentScene, sceneProgress }) => {
  return (
    <div
      style={{
        position: 'absolute',
        top: 0,
        left: 0,
        width: '100%',
        height: '100%',
        pointerEvents: 'none',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
        alignItems: 'center',
      }}
    >
      {/* Scene 4 - Prompt Typing */}
      {currentScene === 'SCENE4' && (
        <PromptTypingOverlay progress={sceneProgress} />
      )}

      {/* Scene 8 - CTA Button */}
      {currentScene === 'SCENE8' && (
        <CTAOverlay progress={sceneProgress} />
      )}

      {/* Scene transitions - fade guides */}
      <SceneTransitionGuide currentScene={currentScene} progress={sceneProgress} />
    </div>
  );
};

interface PromptTypingOverlayProps {
  progress: number;
}

const PromptTypingOverlay: React.FC<PromptTypingOverlayProps> = ({ progress }) => {
  const promptText = 'Build an AI sales system';
  const typedLength = Math.floor(progress * 1.5 * promptText.length); // Fast typing
  const displayText = promptText.substring(0, Math.min(typedLength, promptText.length));

  return (
    <div
      style={{
        position: 'absolute',
        bottom: 200,
        left: '50%',
        transform: 'translateX(-50%)',
        opacity: Math.min(progress * 2, 1),
      }}
    >
      <div
        style={{
          fontFamily: BRAND.typography.fontFamily.body,
          fontSize: 24,
          color: BRAND.colors.white,
          backgroundColor: 'rgba(13, 13, 13, 0.8)',
          padding: '20px 30px',
          borderRadius: BRAND.borderRadius.md,
          border: `1px solid ${BRAND.colors.primary}`,
          backdropFilter: 'blur(10px)',
          minHeight: 60,
          display: 'flex',
          alignItems: 'center',
        }}
      >
        {displayText}
        <span
          style={{
            display: 'inline-block',
            width: '2px',
            height: '24px',
            backgroundColor: BRAND.colors.primary,
            marginLeft: '4px',
            animation: 'blink 1s infinite',
          }}
        />
      </div>

      <style>{`
        @keyframes blink {
          0%, 49%, 100% { opacity: 1; }
          50%, 99% { opacity: 0; }
        }
      `}</style>
    </div>
  );
};

interface CTAOverlayProps {
  progress: number;
}

const CTAOverlay: React.FC<CTAOverlayProps> = ({ progress }) => {
  const titleOpacity = Math.max(0, Math.min((progress - 0.1) * 3, 1));
  const buttonOpacity = Math.max(0, Math.min((progress - 0.3) * 3, 1));
  const buttonScale = 0.8 + Math.min((progress - 0.3) * 2, 1) * 0.2;

  return (
    <div
      style={{
        position: 'absolute',
        bottom: '15%',
        left: '50%',
        transform: 'translateX(-50%)',
        textAlign: 'center',
      }}
    >
      {/* Title */}
      <h1
        style={{
          fontFamily: BRAND.typography.fontFamily.display,
          fontSize: 48,
          fontWeight: BRAND.typography.weights.extrabold,
          color: BRAND.colors.white,
          marginBottom: 30,
          opacity: titleOpacity,
          transition: 'opacity 0.3s',
          letterSpacing: '-1px',
        }}
      >
        Master AI in One Weekend
      </h1>

      {/* Subtitle */}
      <p
        style={{
          fontFamily: BRAND.typography.fontFamily.body,
          fontSize: 16,
          color: BRAND.colors.lightGray,
          marginBottom: 40,
          maxWidth: 500,
          margin: '0 auto 40px',
          opacity: Math.max(0, titleOpacity - 0.2),
        }}
      >
        Build real AI systems. Get hired. Transform your career.
      </p>

      {/* CTA Button */}
      <button
        style={{
          fontFamily: BRAND.typography.fontFamily.display,
          fontSize: 16,
          fontWeight: BRAND.typography.weights.bold,
          color: BRAND.colors.white,
          backgroundColor: BRAND.colors.primary,
          border: 'none',
          padding: '16px 40px',
          borderRadius: BRAND.borderRadius.md,
          cursor: 'pointer',
          opacity: buttonOpacity,
          transform: `scale(${buttonScale})`,
          transition: 'all 0.2s ease-out',
          boxShadow: `0 0 40px rgba(212, 43, 43, ${buttonOpacity * 0.5})`,
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.backgroundColor = BRAND.colors.deepRed;
          e.currentTarget.style.transform = `scale(${buttonScale * 1.05})`;
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.backgroundColor = BRAND.colors.primary;
          e.currentTarget.style.transform = `scale(${buttonScale})`;
        }}
      >
        Join Free Webinar →
      </button>
    </div>
  );
};

interface SceneTransitionGuideProps {
  currentScene: keyof typeof SCENES;
  progress: number;
}

const SceneTransitionGuide: React.FC<SceneTransitionGuideProps> = ({
  currentScene,
  progress,
}) => {
  // Fade between scenes
  const fadeOut = progress > 0.9 ? (progress - 0.9) * 10 : 0;

  return (
    <div
      style={{
        position: 'absolute',
        top: 0,
        left: 0,
        width: '100%',
        height: '100%',
        backgroundColor: `rgba(0, 0, 0, ${fadeOut * 0.3})`,
        opacity: fadeOut,
        pointerEvents: 'none',
      }}
    />
  );
};

// Final AI System - premium visual for scene 7
const FinalAISystem: React.FC<{ progress: number }> = ({ progress }) => {
  return (
    <group>
      {/* Central glowing sphere representing the AI system */}
      <mesh>
        <sphereGeometry args={[3, 64, 64]} />
        <meshStandardMaterial
          color={BRAND.colors.primary}
          emissive={BRAND.colors.primary}
          emissiveIntensity={progress}
          metalness={0.8}
          roughness={0.2}
        />
      </mesh>

      {/* Outer glow sphere */}
      <mesh>
        <sphereGeometry args={[3.2, 32, 32]} />
        <meshBasicMaterial
          color={BRAND.colors.primary}
          transparent
          opacity={progress * 0.4}
        />
      </mesh>

      {/* Rotating rings */}
      <group rotation={[progress * 0.5, progress * 0.3, progress * 0.4]}>
        <mesh>
          <torusGeometry args={[5, 0.3, 16, 100]} />
          <meshStandardMaterial
            color={BRAND.colors.brightRed}
            emissive={BRAND.colors.brightRed}
            emissiveIntensity={0.5}
          />
        </mesh>

        <mesh rotation={[Math.PI / 2, 0, 0]}>
          <torusGeometry args={[5.5, 0.2, 16, 100]} />
          <meshStandardMaterial
            color={BRAND.colors.primary}
            emissive={BRAND.colors.primary}
            emissiveIntensity={0.4}
          />
        </mesh>
      </group>
    </group>
  );
};

export default HeroAnimation;
