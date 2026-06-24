import React, { useRef } from 'react';
import * as THREE from 'three';
import { useFrame } from '@react-three/fiber';
import { useCurrentFrame } from 'remotion';
import { interpolate } from 'remotion';

interface LightingSetupProps {
  intensity?: number;
  scene?: number; // Which scene we're in (1-8)
}

export const LightingSetup: React.FC<LightingSetupProps> = ({ intensity = 1, scene = 1 }) => {
  const keyLightRef = useRef<THREE.DirectionalLight>(null);
  const rimLightRef = useRef<THREE.DirectionalLight>(null);
  const ambientLightRef = useRef<THREE.AmbientLight>(null);
  const pointLightRef = useRef<THREE.PointLight>(null);
  const frame = useCurrentFrame();

  // Dynamic lighting based on scene
  const getLightingValues = () => {
    switch (scene) {
      case 1: // AI World - high ambient, dramatic key
        return {
          keyIntensity: 1.5,
          rimIntensity: 0.8,
          ambientIntensity: 0.6,
          pointIntensity: 1.2,
          pointHeight: 50,
        };
      case 2: // Target Acquisition - focus lighting
        return {
          keyIntensity: 1.8,
          rimIntensity: 0.6,
          ambientIntensity: 0.5,
          pointIntensity: 1.5,
          pointHeight: 40,
        };
      case 3: // Deep Zoom - rapid transition
        return {
          keyIntensity: 2,
          rimIntensity: 0.7,
          ambientIntensity: 0.4,
          pointIntensity: 2,
          pointHeight: 30,
        };
      case 4: // AI Activation - glow and energy
        return {
          keyIntensity: 1.5,
          rimIntensity: 1.2,
          ambientIntensity: 0.7,
          pointIntensity: 3,
          pointHeight: 20,
        };
      case 5: // Explosion - bright and scattered
        return {
          keyIntensity: 2.2,
          rimIntensity: 1,
          ambientIntensity: 0.8,
          pointIntensity: 2.5,
          pointHeight: 25,
        };
      case 6: // Reassembly - focused convergence
        return {
          keyIntensity: 1.8,
          rimIntensity: 0.9,
          ambientIntensity: 0.6,
          pointIntensity: 2.8,
          pointHeight: 22,
        };
      case 7: // Result - premium lighting
        return {
          keyIntensity: 1.6,
          rimIntensity: 1.3,
          ambientIntensity: 0.5,
          pointIntensity: 3.2,
          pointHeight: 15,
        };
      case 8: // CTA - clean and inviting
        return {
          keyIntensity: 1.4,
          rimIntensity: 0.7,
          ambientIntensity: 0.6,
          pointIntensity: 2,
          pointHeight: 20,
        };
      default:
        return {
          keyIntensity: 1.5,
          rimIntensity: 0.8,
          ambientIntensity: 0.6,
          pointIntensity: 1.5,
          pointHeight: 30,
        };
    }
  };

  const lighting = getLightingValues();

  useFrame(() => {
    if (keyLightRef.current) {
      keyLightRef.current.intensity = lighting.keyIntensity * intensity;

      // Dynamic key light rotation for cinematic feel
      const time = frame * 0.001;
      keyLightRef.current.position.x = Math.sin(time * 0.3) * 30;
      keyLightRef.current.position.y = 40 + Math.cos(time * 0.2) * 10;
      keyLightRef.current.position.z = Math.cos(time * 0.3) * 30;
    }

    if (rimLightRef.current) {
      rimLightRef.current.intensity = lighting.rimIntensity * intensity;

      // Opposite side of key light
      const time = frame * 0.001;
      rimLightRef.current.position.x = -Math.sin(time * 0.3) * 25;
      rimLightRef.current.position.y = 30;
      rimLightRef.current.position.z = -Math.cos(time * 0.3) * 25;
    }

    if (ambientLightRef.current) {
      ambientLightRef.current.intensity = lighting.ambientIntensity * intensity;
    }

    if (pointLightRef.current) {
      pointLightRef.current.intensity = lighting.pointIntensity * intensity;
      pointLightRef.current.position.y = lighting.pointHeight;

      // Orbit point light
      const time = frame * 0.002;
      pointLightRef.current.position.x = Math.cos(time) * 30;
      pointLightRef.current.position.z = Math.sin(time) * 30;
    }
  });

  return (
    <>
      {/* Ambient light - overall illumination */}
      <ambientLight
        ref={ambientLightRef}
        color="#FFFFFF"
        intensity={lighting.ambientIntensity * intensity}
      />

      {/* Key light - main directional */}
      <directionalLight
        ref={keyLightRef}
        color="#FFFFFF"
        intensity={lighting.keyIntensity * intensity}
        position={[30, 40, 30]}
        castShadow
        shadow-mapSize-width={2048}
        shadow-mapSize-height={2048}
        shadow-camera-left={-100}
        shadow-camera-right={100}
        shadow-camera-top={100}
        shadow-camera-bottom={-100}
      />

      {/* Rim light - edge definition */}
      <directionalLight
        ref={rimLightRef}
        color="#FF4040"
        intensity={lighting.rimIntensity * intensity}
        position={[-25, 30, -25]}
      />

      {/* Point light - focal accent */}
      <pointLight
        ref={pointLightRef}
        color="#D42B2B"
        intensity={lighting.pointIntensity * intensity}
        distance={100}
        decay={2}
        position={[30, lighting.pointHeight, 30]}
        castShadow
      />

      {/* Fill light - subtle overall brightening */}
      <directionalLight
        color="#D42B2B"
        intensity={0.3 * intensity}
        position={[0, -20, 40]}
      />

      {/* GodRays simulation with point light */}
      <pointLight
        color="#FFFFFF"
        intensity={0.5 * intensity}
        distance={200}
        position={[0, 80, 0]}
      />
    </>
  );
};

// Specialized lighting for different scenes
export const Scene1Lighting: React.FC = () => <LightingSetup scene={1} />;
export const Scene2Lighting: React.FC = () => <LightingSetup scene={2} />;
export const Scene3Lighting: React.FC = () => <LightingSetup scene={3} />;
export const Scene4Lighting: React.FC = () => <LightingSetup scene={4} />;
export const Scene5Lighting: React.FC = () => <LightingSetup scene={5} />;
export const Scene6Lighting: React.FC = () => <LightingSetup scene={6} />;
export const Scene7Lighting: React.FC = () => <LightingSetup scene={7} />;
export const Scene8Lighting: React.FC = () => <LightingSetup scene={8} />;

export default LightingSetup;
