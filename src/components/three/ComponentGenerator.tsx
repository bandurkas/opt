import React, { useMemo, useRef } from 'react';
import * as THREE from 'three';
import { useFrame, useThree } from '@react-three/fiber';
import { useCurrentFrame } from 'remotion';

interface ComponentData {
  id: string;
  label: string;
  icon: string;
  targetX: number;
  targetY: number;
  targetZ: number;
  color: string;
  size: number;
  phase: number;
}

interface ComponentGeneratorProps {
  isExploded: boolean;
  isReassembling: boolean;
  progress: number;
}

export const ComponentGenerator: React.FC<ComponentGeneratorProps> = ({
  isExploded,
  isReassembling,
  progress,
}) => {
  const frame = useCurrentFrame();
  const groupRef = useRef<THREE.Group>(null);
  const componentRefs = useRef<THREE.Mesh[]>([]);

  // Generate AI system components
  const components: ComponentData[] = useMemo(() => {
    const data: ComponentData[] = [
      { id: 'llm', label: 'LLM', icon: '🧠', targetX: 0, targetY: 5, targetZ: 0, color: '#D42B2B', size: 2, phase: 0 },
      { id: 'memory', label: 'Memory', icon: '💾', targetX: 8, targetY: 0, targetZ: -5, color: '#FF4040', size: 1.5, phase: Math.PI / 6 },
      { id: 'automation', label: 'Automation', icon: '⚙️', targetX: -8, targetY: 0, targetZ: -5, color: '#A81E1E', size: 1.5, phase: Math.PI / 3 },
      { id: 'crm', label: 'CRM', icon: '📊', targetX: 4, targetY: -6, targetZ: 4, color: '#FF4040', size: 1.3, phase: Math.PI / 2 },
      { id: 'api', label: 'API', icon: '🔌', targetX: -4, targetY: -6, targetZ: 4, color: '#D42B2B', size: 1.3, phase: Math.PI * 2 / 3 },
      { id: 'analytics', label: 'Analytics', icon: '📈', targetX: 6, targetY: -3, targetZ: -8, color: '#FF4040', size: 1.2, phase: Math.PI * 5 / 6 },
      { id: 'workflow', label: 'Workflow', icon: '🔄', targetX: -6, targetY: -3, targetZ: -8, color: '#A81E1E', size: 1.2, phase: Math.PI },
      { id: 'database', label: 'Database', icon: '🗄️', targetX: 0, targetY: -8, targetZ: 0, color: '#D42B2B', size: 1.8, phase: Math.PI * 7 / 6 },
      { id: 'leads', label: 'Lead Gen', icon: '👥', targetX: 5, targetY: 2, targetZ: 8, color: '#FF4040', size: 1.2, phase: Math.PI * 4 / 3 },
      { id: 'email', label: 'Email', icon: '📧', targetX: -5, targetY: 2, targetZ: 8, color: '#A81E1E', size: 1.2, phase: Math.PI * 3 / 2 },
      { id: 'whatsapp', label: 'WhatsApp', icon: '💬', targetX: 8, targetY: 3, targetZ: 3, color: '#D42B2B', size: 1, phase: Math.PI * 5 / 3 },
      { id: 'sales', label: 'Sales', icon: '💰', targetX: -8, targetY: 3, targetZ: 3, color: '#FF4040', size: 1.1, phase: Math.PI * 11 / 6 },
    ];
    return data;
  }, []);

  // Calculate positions based on state
  const getComponentPosition = (component: ComponentData) => {
    if (isExploded) {
      // Exploded state: components float in space
      const angle = Math.atan2(component.targetZ, component.targetX);
      const distance = Math.sqrt(component.targetX ** 2 + component.targetZ ** 2) * 3;
      const driftX = Math.cos(angle) * distance + Math.sin(frame * 0.02 + component.phase) * 5;
      const driftY = component.targetY * 2 + Math.cos(frame * 0.015 + component.phase) * 8;
      const driftZ = Math.sin(angle) * distance + Math.cos(frame * 0.018 + component.phase) * 5;
      return [driftX, driftY, driftZ];
    }

    if (isReassembling) {
      // Reassembling: smoothly move back to final position
      const startX = Math.cos(component.phase) * 20;
      const startY = Math.sin(component.phase) * 15;
      const startZ = Math.cos(component.phase + Math.PI / 2) * 20;

      return [
        startX + (component.targetX - startX) * progress,
        startY + (component.targetY - startY) * progress,
        startZ + (component.targetZ - startZ) * progress,
      ];
    }

    // Normal state: arranged around center
    return [component.targetX, component.targetY, component.targetZ];
  };

  useFrame(() => {
    components.forEach((component, i) => {
      const mesh = componentRefs.current[i];
      if (!mesh) return;

      const [x, y, z] = getComponentPosition(component);
      mesh.position.set(x, y, z);

      // Rotation
      if (isExploded) {
        mesh.rotation.x += 0.005;
        mesh.rotation.y += 0.007;
        mesh.rotation.z += 0.003;
      } else {
        mesh.rotation.x += 0.001;
        mesh.rotation.y += 0.002;
      }

      // Scale pulse
      const scale = component.size * (1 + Math.sin(frame * 0.05 + component.phase) * 0.1);
      mesh.scale.set(scale, scale, scale);
    });
  });

  return (
    <group ref={groupRef}>
      {components.map((component, i) => (
        <ComponentBox
          key={component.id}
          component={component}
          ref={(mesh) => {
            if (mesh) componentRefs.current[i] = mesh;
          }}
        />
      ))}

      {/* Connection lines (visible during reassembly) */}
      {isReassembling && progress > 0.3 && (
        <ComponentConnections components={components} progress={progress} />
      )}
    </group>
  );
};

interface ComponentBoxProps {
  component: ComponentData;
}

const ComponentBox = React.forwardRef<THREE.Mesh, ComponentBoxProps>(
  ({ component }, ref) => {
    return (
      <group ref={ref}>
        {/* Main box */}
        <mesh castShadow receiveShadow>
          <boxGeometry args={[1, 1, 1]} />
          <meshStandardMaterial
            color={component.color}
            emissive={component.color}
            emissiveIntensity={0.3}
            metalness={0.7}
            roughness={0.3}
          />
        </mesh>

        {/* Glow effect */}
        <mesh>
          <boxGeometry args={[1.1, 1.1, 1.1]} />
          <meshBasicMaterial
            color={component.color}
            transparent
            opacity={0.2}
          />
        </mesh>

        {/* Label */}
        <Html position={[0, -0.8, 0]}>
          <div
            style={{
              color: '#FFFFFF',
              fontSize: '10px',
              fontWeight: 'bold',
              whiteSpace: 'nowrap',
              textAlign: 'center',
            }}
          >
            {component.label}
          </div>
        </Html>
      </group>
    );
  }
);

ComponentBox.displayName = 'ComponentBox';

interface ComponentConnectionsProps {
  components: ComponentData[];
  progress: number;
}

const ComponentConnections: React.FC<ComponentConnectionsProps> = ({ components, progress }) => {
  const connections = useMemo(() => {
    const lines: Array<[ComponentData, ComponentData]> = [];

    // Connect nearby components
    for (let i = 0; i < components.length; i++) {
      for (let j = i + 1; j < components.length; j++) {
        const dist = Math.sqrt(
          Math.pow(components[i].targetX - components[j].targetX, 2) +
          Math.pow(components[i].targetY - components[j].targetY, 2) +
          Math.pow(components[i].targetZ - components[j].targetZ, 2)
        );

        // Only connect nearby components
        if (dist < 15) {
          lines.push([components[i], components[j]]);
        }
      }
    }

    return lines;
  }, [components]);

  return (
    <group>
      {connections.map((connection, i) => (
        <ConnectionLine
          key={i}
          from={connection[0]}
          to={connection[1]}
          opacity={progress}
        />
      ))}
    </group>
  );
};

interface ConnectionLineProps {
  from: ComponentData;
  to: ComponentData;
  opacity: number;
}

const ConnectionLine: React.FC<ConnectionLineProps> = ({ from, to, opacity }) => {
  const points = [
    new THREE.Vector3(from.targetX, from.targetY, from.targetZ),
    new THREE.Vector3(to.targetX, to.targetY, to.targetZ),
  ];

  const geometry = new THREE.BufferGeometry().setFromPoints(points);

  return (
    <line geometry={geometry}>
      <lineBasicMaterial
        color="#D42B2B"
        transparent
        opacity={opacity * 0.5}
        linewidth={2}
      />
    </line>
  );
};

// Html component import (simplified)
const Html: React.FC<{ position: [number, number, number]; children: React.ReactNode }> = ({
  position,
  children,
}) => {
  return <>{children}</>;
};

export default ComponentGenerator;
