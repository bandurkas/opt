import React from 'react';
import { useFrame } from '@react-three/fiber';
import { PerspectiveCamera } from '@react-three/drei';
import * as THREE from 'three';
import { useCurrentFrame, interpolate, Easing } from 'remotion';
import { EasingFunctions, cinematicDrift } from '../../utils/easingFunctions';

interface CameraKeyframe {
  frame: number;
  position: [number, number, number];
  lookAt: [number, number, number];
  fov?: number;
}

interface CameraControllerProps {
  keyframes: CameraKeyframe[];
  enableDrift?: boolean;
  enableShake?: boolean;
  shakeIntensity?: number;
}

export const CameraController: React.FC<CameraControllerProps> = ({
  keyframes,
  enableDrift = true,
  enableShake = false,
  shakeIntensity = 0.1,
}) => {
  const frame = useCurrentFrame();
  const cameraRef = React.useRef<THREE.PerspectiveCamera>(null);
  const controlsRef = React.useRef<any>(null);

  // Interpolate camera position between keyframes
  const getPositionAtFrame = (frame: number) => {
    for (let i = 0; i < keyframes.length - 1; i++) {
      const current = keyframes[i];
      const next = keyframes[i + 1];

      if (frame >= current.frame && frame < next.frame) {
        const progress = (frame - current.frame) / (next.frame - current.frame);
        const easedProgress = EasingFunctions.cinematicCamera(progress);

        return {
          x: current.position[0] + (next.position[0] - current.position[0]) * easedProgress,
          y: current.position[1] + (next.position[1] - current.position[1]) * easedProgress,
          z: current.position[2] + (next.position[2] - current.position[2]) * easedProgress,
        };
      }
    }

    // Return last position
    const last = keyframes[keyframes.length - 1];
    return {
      x: last.position[0],
      y: last.position[1],
      z: last.position[2],
    };
  };

  // Interpolate look-at target
  const getLookAtTarget = (frame: number) => {
    for (let i = 0; i < keyframes.length - 1; i++) {
      const current = keyframes[i];
      const next = keyframes[i + 1];

      if (frame >= current.frame && frame < next.frame) {
        const progress = (frame - current.frame) / (next.frame - current.frame);
        const easedProgress = EasingFunctions.naturalMotion(progress);

        return {
          x: current.lookAt[0] + (next.lookAt[0] - current.lookAt[0]) * easedProgress,
          y: current.lookAt[1] + (next.lookAt[1] - current.lookAt[1]) * easedProgress,
          z: current.lookAt[2] + (next.lookAt[2] - current.lookAt[2]) * easedProgress,
        };
      }
    }

    const last = keyframes[keyframes.length - 1];
    return {
      x: last.lookAt[0],
      y: last.lookAt[1],
      z: last.lookAt[2],
    };
  };

  // Get FOV at frame
  const getFOVAtFrame = (frame: number) => {
    for (let i = 0; i < keyframes.length - 1; i++) {
      const current = keyframes[i];
      const next = keyframes[i + 1];

      if (frame >= current.frame && frame < next.frame) {
        const progress = (frame - current.frame) / (next.frame - current.frame);
        const currentFOV = current.fov ?? 75;
        const nextFOV = next.fov ?? 75;
        return currentFOV + (nextFOV - currentFOV) * progress;
      }
    }

    const last = keyframes[keyframes.length - 1];
    return last.fov ?? 75;
  };

  useFrame(() => {
    if (!cameraRef.current) return;

    const pos = getPositionAtFrame(frame);
    const target = getLookAtTarget(frame);
    const fov = getFOVAtFrame(frame);

    // Apply drift if enabled
    const driftAmount = enableDrift ? 0.3 : 0;
    const driftedX = cinematicDrift(pos.x, driftAmount, frame);
    const driftedY = cinematicDrift(pos.y, driftAmount, frame);

    // Apply shake if enabled
    let shakeX = 0;
    let shakeY = 0;
    let shakeZ = 0;
    if (enableShake) {
      shakeX = (Math.random() - 0.5) * shakeIntensity;
      shakeY = (Math.random() - 0.5) * shakeIntensity;
      shakeZ = (Math.random() - 0.5) * shakeIntensity;
    }

    // Update camera
    cameraRef.current.position.set(
      driftedX + shakeX,
      driftedY + shakeY,
      pos.z + shakeZ
    );
    cameraRef.current.fov = fov;
    cameraRef.current.updateProjectionMatrix();

    // Update look-at target
    cameraRef.current.lookAt(target.x, target.y, target.z);
  });

  return <PerspectiveCamera ref={cameraRef} makeDefault fov={75} />;
};

// Preset camera keyframes for different scenes
export const CAMERA_KEYFRAMES = {
  // Scene 1: High above the world, establishing shot
  scene1: [
    {
      frame: 0,
      position: [0, 100, 150] as [number, number, number],
      lookAt: [0, 0, 0] as [number, number, number],
      fov: 45,
    },
    {
      frame: 150,
      position: [80, 120, 180] as [number, number, number],
      lookAt: [0, 30, 0] as [number, number, number],
      fov: 50,
    },
  ],

  // Scene 2: Target lock - slow approach
  scene2: [
    {
      frame: 150,
      position: [80, 120, 180] as [number, number, number],
      lookAt: [0, 30, 0] as [number, number, number],
      fov: 50,
    },
    {
      frame: 300,
      position: [40, 80, 120] as [number, number, number],
      lookAt: [0, 20, 0] as [number, number, number],
      fov: 55,
    },
  ],

  // Scene 3: Deep zoom - rapid acceleration
  scene3: [
    {
      frame: 300,
      position: [40, 80, 120] as [number, number, number],
      lookAt: [0, 20, 0] as [number, number, number],
      fov: 55,
    },
    {
      frame: 450,
      position: [10, 40, 50] as [number, number, number],
      lookAt: [0, 15, 0] as [number, number, number],
      fov: 70,
    },
  ],

  // Scene 4: Zoom to interface level
  scene4: [
    {
      frame: 450,
      position: [10, 40, 50] as [number, number, number],
      lookAt: [0, 15, 0] as [number, number, number],
      fov: 70,
    },
    {
      frame: 600,
      position: [0, 0, 5] as [number, number, number],
      lookAt: [0, 0, 0] as [number, number, number],
      fov: 75,
    },
  ],

  // Scene 5: Floating through components
  scene5: [
    {
      frame: 600,
      position: [0, 0, 5] as [number, number, number],
      lookAt: [0, 0, 0] as [number, number, number],
      fov: 75,
    },
    {
      frame: 750,
      position: [20, 15, 25] as [number, number, number],
      lookAt: [0, 0, 0] as [number, number, number],
      fov: 65,
    },
  ],

  // Scene 6: Circle around assembling system
  scene6: [
    {
      frame: 750,
      position: [20, 15, 25] as [number, number, number],
      lookAt: [0, 0, 0] as [number, number, number],
      fov: 65,
    },
    {
      frame: 960,
      position: [-25, 20, -15] as [number, number, number],
      lookAt: [0, 0, 0] as [number, number, number],
      fov: 60,
    },
  ],

  // Scene 7: Final reveal - centered view
  scene7: [
    {
      frame: 960,
      position: [-25, 20, -15] as [number, number, number],
      lookAt: [0, 0, 0] as [number, number, number],
      fov: 60,
    },
    {
      frame: 1140,
      position: [0, 5, 25] as [number, number, number],
      lookAt: [0, 0, 0] as [number, number, number],
      fov: 75,
    },
  ],

  // Scene 8: CTA settle
  scene8: [
    {
      frame: 1140,
      position: [0, 5, 25] as [number, number, number],
      lookAt: [0, 0, 0] as [number, number, number],
      fov: 75,
    },
    {
      frame: 1350,
      position: [0, 2, 20] as [number, number, number],
      lookAt: [0, 0, 0] as [number, number, number],
      fov: 75,
    },
  ],
} as const;
