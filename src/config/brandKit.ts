// GDI FutureWorks Brand Kit
export const BRAND = {
  colors: {
    primary: '#D42B2B',      // GDI Red
    deepRed: '#A81E1E',       // Hover, Shadows
    brightRed: '#FF4040',     // Highlights
    black: '#0D0D0D',         // Carbon Black
    white: '#FFFFFF',
    lightGray: '#F2F2F2',
    darkGray: '#555555',
  },

  typography: {
    fontFamily: {
      display: '"Plus Jakarta Sans", sans-serif',
      body: '"Poppins", sans-serif',
    },
    sizes: {
      h1: 48,
      h2: 28,
      h3: 18,
      body: 14,
      caption: 10,
    },
    weights: {
      thin: 300,
      regular: 400,
      medium: 500,
      semibold: 600,
      bold: 700,
      extrabold: 800,
    },
  },

  spacing: {
    xs: 8,
    sm: 12,
    md: 16,
    lg: 24,
    xl: 32,
    xxl: 48,
  },

  borderRadius: {
    sm: 8,
    md: 12,
    lg: 16,
  },

  shadows: {
    sm: '0 2px 8px rgba(0,0,0,0.1)',
    md: '0 8px 24px rgba(0,0,0,0.2)',
    lg: '0 20px 40px rgba(0,0,0,0.3)',
    glow: '0 0 40px rgba(212,43,43,0.4)',
  },
} as const;

export type BrandColor = typeof BRAND.colors;
