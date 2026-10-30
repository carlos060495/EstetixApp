/** @type {import('next').NextConfig} */
const nextConfig = {
  // Permite acceso al dev server desde IPs de red local (móvil, tablet, otro PC).
  // Añade tu IP en .env.local: NEXT_DEV_ALLOWED_ORIGINS=192.168.1.17
  // Si cambia la IP, actualiza solo .env.local (nunca este archivo).
  allowedDevOrigins: process.env.NEXT_DEV_ALLOWED_ORIGINS
    ? process.env.NEXT_DEV_ALLOWED_ORIGINS.split(",").map((s) => s.trim())
    : [],
};

export default nextConfig;
