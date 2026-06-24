import React, { useMemo, useRef } from 'react';
import * as THREE from 'three';
import { useFrame } from '@react-three/fiber';
import { InstancedMesh } from '@react-three/drei';

interface BuildingConfig {
  x: number;
  y: number;
  z: number;
  scaleX: number;
  scaleY: number;
  scaleZ: number;
  color: number;
}

export const AIWorldGenerator: React.FC = () => {
  const groupRef = useRef<THREE.Group>(null);
  const buildingsRef = useRef<THREE.InstancedMesh>(null);

  // Generate building layouts
  const buildings = useMemo(() => {
    const configs: BuildingConfig[] = [];
    const count = 50;

    for (let i = 0; i < count; i++) {
      const angle = (i / count) * Math.PI * 2;
      const radius = 40 + Math.random() * 60;
      const x = Math.cos(angle) * radius;
      const z = Math.sin(angle) * radius;
      const y = Math.random() * 40;
      const height = 20 + Math.random() * 80;

      configs.push({
        x,
        y: y + height / 2,
        z,
        scaleX: 3 + Math.random() * 5,
        scaleY: height,
        scaleZ: 3 + Math.random() * 5,
        color: new THREE.Color(`hsl(0, 100%, ${40 + Math.random() * 30}%)`).getHex(),
      });
    }

    return configs;
  }, []);

  // Animate buildings
  useFrame(({ clock }) => {
    if (buildingsRef.current) {
      const time = clock.getElapsedTime();
      const dummy = new THREE.Object3D();

      buildings.forEach((building, i) => {
        // Subtle height pulsing
        const pulse = Math.sin(time * 2 + i * 0.1) * 2;

        dummy.position.set(building.x, building.y + pulse, building.z);
        dummy.scale.set(
          building.scaleX,
          building.scaleY,
          building.scaleZ
        );
        dummy.updateMatrix();
        buildingsRef.current?.setMatrixAt(i, dummy.matrix);
      });

      buildingsRef.current.instanceMatrix.needsUpdate = true;
    }
  });

  return (
    <group ref={groupRef}>
      {/* Buildings */}
      <InstancedMesh
        ref={buildingsRef}
        args={[
          new THREE.BoxGeometry(1, 1, 1),
          new THREE.MeshStandardMaterial({
            color: '#D42B2B',
            emissive: '#A81E1E',
            emissiveIntensity: 0.3,
            metalness: 0.6,
            roughness: 0.4,
          }),
          buildings.length,
        ]}
      />

      {/* Ground plane with grid */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.5, 0]}>
        <planeGeometry args={[200, 200]} />
        <meshStandardMaterial
          color="#0D0D0D"
          metalness={0.1}
          roughness={0.8}
        />
      </mesh>

      {/* Grid visualization */}
      <GridLines />

      {/* Floating nodes/networks */}
      <FloatingNodes />

      {/* Data streams */}
      <DataStreams />
    </group>
  );
};

// Grid visualization for digital city feel
const GridLines: React.FC = () => {
  const gridSize = 200;
  const gridDivisions = 20;
  const step = gridSize / gridDivisions;

  const positions: number[] = [];

  // Vertical lines (X direction)
  for (let i = -gridSize / 2; i <= gridSize / 2; i += step) {
    positions.push(i, 0, -gridSize / 2);
    positions.push(i, 0, gridSize / 2);
  }

  // Horizontal lines (Z direction)
  for (let i = -gridSize / 2; i <= gridSize / 2; i += step) {
    positions.push(-gridSize / 2, 0, i);
    positions.push(gridSize / 2, 0, i);
  }

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(positions), 3));

  return (
    <lineSegments geometry={geometry}>
      <lineBasicMaterial color="#D42B2B" linewidth={1} transparent opacity={0.3} />
    </lineSegments>
  );
};

// Floating nodes representing network connections
const FloatingNodes: React.FC = () => {
  const nodeRefs = useRef<THREE.Mesh[]>([]);
  const nodeCount = 30;

  const nodes = useMemo(() => {
    return Array.from({ length: nodeCount }, (_, i) => ({
      id: i,
      startX: (Math.random() - 0.5) * 150,
      startY: Math.random() * 80,
      startZ: (Math.random() - 0.5) * 150,
      speedX: (Math.random() - 0.5) * 0.02,
      speedY: (Math.random() - 0.5) * 0.01,
      speedZ: (Math.random() - 0.5) * 0.02,
      size: 0.5 + Math.random() * 1.5,
      phase: Math.random() * Math.PI * 2,
    }));
  }, []);

  useFrame(({ clock }) => {
    const time = clock.getElapsedTime();

    nodes.forEach((node, i) => {
      const mesh = nodeRefs.current[i];
      if (!mesh) return;

      mesh.position.x = node.startX + Math.sin(time * 0.3 + node.phase) * 10;
      mesh.position.y = node.startY + Math.cos(time * 0.2 + node.phase) * 5;
      mesh.position.z = node.startZ + Math.sin(time * 0.25 + node.phase) * 10;

      // Pulse opacity
      (mesh.material as THREE.Material).opacity = 0.5 + Math.sin(time * 2 + node.phase) * 0.3;
    });
  });

  return (
    <group>
      {nodes.map((node) => (
        <mesh
          key={node.id}
          position={[node.startX, node.startY, node.startZ]}
          ref={(mesh) => {
            if (mesh) nodeRefs.current[node.id] = mesh;
          }}
        >
          <sphereGeometry args={[node.size, 16, 16]} />
          <meshStandardMaterial
            color="#FF4040"
            emissive="#D42B2B"
            emissiveIntensity={0.5}
            transparent
            opacity={0.7}
          />
        </mesh>
      ))}
    </group>
  );
};

// Animated data streams
const DataStreams: React.FC = () => {
  const streamCount = 10;

  return (
    <group>
      {Array.from({ length: streamCount }).map((_, i) => (
        <DataStream key={i} index={i} />
      ))}
    </group>
  );
};

interface DataStreamProps {
  index: number;
}

const DataStream: React.FC<DataStreamProps> = ({ index }) => {
  const meshRef = useRef<THREE.Line>(null);

  const streamPath = useMemo(() => {
    const points: THREE.Vector3[] = [];
    const segments = 50;

    const angle = (index / 10) * Math.PI * 2;
    const radiusStart = 30;
    const radiusEnd = 80;

    for (let i = 0; i <= segments; i++) {
      const progress = i / segments;
      const radius = radiusStart + (radiusEnd - radiusStart) * progress;
      const height = Math.sin(progress * Math.PI) * 40;
      const x = Math.cos(angle) * radius;
      const z = Math.sin(angle) * radius;

      points.push(new THREE.Vector3(x, height, z));
    }

    return points;
  }, [index]);

  useFrame(({ clock }) => {
    if (!meshRef.current) return;

    const time = clock.getElapsedTime();
    meshRef.current.rotation.y = time * 0.3 + index * 0.2;
  });

  const geometry = new THREE.BufferGeometry().setFromPoints(streamPath);

  return (
    <line ref={meshRef} geometry={geometry}>
      <lineBasicMaterial color="#D42B2B" linewidth={2} transparent opacity={0.6} />
    </line>
  );
};

export default AIWorldGenerator;
