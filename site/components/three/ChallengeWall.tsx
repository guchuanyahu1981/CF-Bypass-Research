'use client';

import { Canvas, useFrame, useThree } from '@react-three/fiber';

const PANELS = [
  { label: 'jsd', x: -2.1, color: '#4DE3C1' },
  { label: 'managed', x: 0, color: '#A78BFA' },
  { label: 'turnstile', x: 2.1, color: '#60A5FA' },
];

function Panel({ x, color, active }: { x: number; color: string; active: boolean }) {
  return (
    <group position={[x, 0.45, 0]}>
      <mesh>
        <boxGeometry args={[1.65, 1.05, 0.07]} />
        <meshStandardMaterial
          color={color}
          emissive={color}
          emissiveIntensity={active ? 0.52 : 0.16}
          roughness={0.42}
          metalness={0.18}
        />
      </mesh>
    </group>
  );
}

function Token({ active }: { active: boolean }) {
  // The clearance token is deliberately static: the design forbids auto-rotating models.
  return (
    <group position={[0, -1.15, 0.1]}>
      <mesh>
        <icosahedronGeometry args={[0.3, 0]} />
        <meshStandardMaterial
          color="#4DE3C1"
          emissive="#4DE3C1"
          emissiveIntensity={active ? 0.85 : 0.6}
          roughness={0.2}
          metalness={0.1}
        />
      </mesh>
    </group>
  );
}

function CameraRig() {
  const { camera, pointer, invalidate } = useThree();
  /* eslint-disable react-hooks/immutability -- R3F cameras are imperative three.js
     objects; mutating position inside useFrame is the documented pattern and the
     React Compiler rule does not model it. */
  useFrame(() => {
    const targetX = pointer.x * 0.35;
    const targetY = pointer.y * 0.18;
    const dx = targetX - camera.position.x;
    const dy = targetY - camera.position.y;
    if (Math.abs(dx) < 0.002 && Math.abs(dy) < 0.002) return;
    camera.position.x += dx * 0.12;
    camera.position.y += dy * 0.12;
    camera.lookAt(0, 0, 0);
    invalidate();
  });
  /* eslint-enable react-hooks/immutability */
  return null;
}

export default function ChallengeWall({ tier = 'full', selected = null }: { tier?: 'full' | 'lite'; selected?: string | null }) {
  return (
    <Canvas
      dpr={tier === 'lite' ? 1 : [1, 1.5]}
      camera={{ position: [0, 0, 6], fov: 45 }}
      gl={{ alpha: true, antialias: true, powerPreference: 'high-performance' }}
      frameloop="demand"
      style={{ width: '100%', height: '100%' }}
    >
      <ambientLight intensity={0.85} />
      <directionalLight position={[3, 4, 5]} intensity={1.15} />
      <pointLight position={[-3, 2, 2]} color="#A78BFA" intensity={2.2} />
      {PANELS.map((panel) => (
        <Panel key={panel.label} x={panel.x} color={panel.color} active={selected === panel.label} />
      ))}
      <mesh rotation={[Math.PI / 2, 0, 0]} position={[0, -0.1, 0]}>
        <cylinderGeometry args={[0.012, 0.012, 5.4, 8]} />
        <meshStandardMaterial color="#4DE3C1" emissive="#4DE3C1" emissiveIntensity={0.55} />
      </mesh>
      <Token active={selected === null} />
      <CameraRig />
    </Canvas>
  );
}