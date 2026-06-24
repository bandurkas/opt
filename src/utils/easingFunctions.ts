import { interpolate, Easing } from 'remotion';

// Custom easing functions for cinematic motion
export const EasingFunctions = {
  // Cinematic camera easing - slow start, smooth acceleration
  cinematicCamera: Easing.cubic(0.25, 0.1, 0.25, 1),

  // Anticipation + overshoot
  elementEntry: Easing.bezier(0.68, -0.55, 0.265, 1.55),

  // Smooth deceleration
  elementExit: Easing.cubic(0.87, 0, 0.13, 1),

  // Spring-like motion for UI elements
  springBounce: Easing.bezier(0.175, 0.885, 0.32, 1.275),

  // Very smooth, natural motion
  naturalMotion: Easing.cubic(0.4, 0.0, 0.2, 1),

  // Aggressive acceleration
  aggressiveAccel: Easing.bezier(0.95, 0.05, 0.795, 0.035),

  // Magnetic attraction
  magneticPull: Easing.cubic(0.39, 0.575, 0.565, 1),

  // Smooth pulse
  pulseFade: Easing.bezier(0.215, 0.61, 0.355, 1),

  // Elastic outgoing motion
  elasticExit: Easing.bezier(0.175, 0.885, 0.32, 1.275),
};

// Helper to create smooth camera paths
export function createSmoothPath(
  startValue: number,
  endValue: number,
  startFrame: number,
  endFrame: number,
  easing: (p: number) => number = EasingFunctions.cinematicCamera
) {
  return interpolate(
    startFrame,
    endFrame,
    startValue,
    endValue,
    { extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing }
  );
}

// Micro-movements for natural animation
export function addMicroMotion(
  baseValue: number,
  amplitude: number = 0.5,
  frequency: number = 0.002,
  time: number = 0
): number {
  return baseValue + Math.sin(time * frequency) * amplitude;
}

// Cinematic drift for camera
export function cinematicDrift(
  baseValue: number,
  driftAmount: number,
  time: number
): number {
  const drift = Math.sin(time * 0.0005) * driftAmount;
  return baseValue + drift;
}

// Spring physics simulation
export function springMotion(
  start: number,
  end: number,
  progress: number,
  tension: number = 0.8,
  friction: number = 0.1
): number {
  // Simplified spring physics
  const displacement = end - start;
  return start + displacement * (1 - Math.pow(2, -10 * progress) * Math.cos(progress * 4 * Math.PI));
}

// Ease between values with optional delay
export function delayedEase(
  startFrame: number,
  endFrame: number,
  startValue: number,
  endValue: number,
  delayFrames: number = 0,
  easing: (p: number) => number = EasingFunctions.naturalMotion
): (frame: number) => number {
  return (frame: number) => {
    if (frame < startFrame + delayFrames) return startValue;
    const adjustedStart = startFrame + delayFrames;
    if (frame > endFrame) return endValue;
    const progress = (frame - adjustedStart) / (endFrame - adjustedStart);
    const easedProgress = easing(Math.min(progress, 1));
    return startValue + (endValue - startValue) * easedProgress;
  };
}
