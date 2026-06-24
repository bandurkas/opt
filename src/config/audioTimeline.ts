// Audio design timeline with exact timestamps (in milliseconds)
export const AUDIO_TIMELINE = {
  // Scene 1: AI World (0-5s) - Ambient rise
  scene1: {
    ambientStart: 0,
    ambientEnd: 5000,
    soundEffect: 'ambient_whoosh_start',
  },

  // Scene 2: Target Acquisition (5-10s) - Pulse and focus
  scene2Start: 5000,
  targetLock: {
    sound: 'target_lock_pulse',
    timestamp: 6500,
    duration: 800,
  },

  // Scene 3: Deep Zoom (10-15s) - Accelerating whoosh
  scene3Start: 10000,
  zoomWhoosh: {
    sound: 'deep_zoom_whoosh',
    timestamp: 10500,
    duration: 4500,
    pitchRise: true,
  },

  // Scene 4: AI Activation (15-20s) - Prompt typing and submission
  scene4Start: 15000,
  typingSounds: {
    sound: 'ui_typewriter_loop',
    startTimestamp: 15500,
    endTimestamp: 18000,
  },
  promptSubmit: {
    sound: 'ui_submit_confirm',
    timestamp: 18500,
    duration: 600,
  },
  transformationPulse: {
    sound: 'energy_pulse_expanding',
    timestamp: 19000,
    duration: 800,
  },

  // Scene 5: Explosion of Components (20-25s) - Controlled disassembly
  scene5Start: 20000,
  disassemblyStart: {
    sound: 'component_scatter_whoosh',
    timestamp: 20500,
    duration: 4500,
  },

  // Scene 6: Reassembly (25-32s) - Magnetic assembly
  scene6Start: 25000,
  reassemblyStart: {
    sound: 'magnetic_attract_hum',
    timestamp: 25500,
    duration: 6500,
  },
  connectionSnaps: {
    sound: 'connection_snap',
    timestamps: [27000, 28000, 29000, 30000, 31000],
  },

  // Scene 7: Result (32-38s) - Glowing transformation
  scene7Start: 32000,
  aiActivation: {
    sound: 'ai_activation_rise',
    timestamp: 32500,
    duration: 5500,
    cinemaricRise: true,
  },

  // Scene 8: CTA (38-45s) - Button reveal
  scene8Start: 38000,
  ctaReveal: {
    sound: 'cta_button_reveal',
    timestamp: 38500,
    duration: 1200,
  },
  ctaHover: {
    sound: 'cta_button_hover',
    // User interaction
  },
  finalAmbience: {
    sound: 'ambient_sustain',
    timestamp: 40000,
    fadeOutStart: 42000,
  },
} as const;

export type AudioMarker = {
  sound: string;
  timestamp: number;
  duration?: number;
};
