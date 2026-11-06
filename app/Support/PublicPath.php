<?php

namespace App\Support;

/**
 * Builds links under the tailnet mount point (tailscale serve strips "/afvi_ai" before the request
 * reaches the app), so pages work both through the tailnet and through a loopback tunnel.
 */
class PublicPath
{
    public static function url(string $path = ''): string
    {
        $prefix = rtrim((string) config('afvi.public_prefix', '/afvi_ai'), '/');
        $path = ltrim($path, '/');

        return $path === '' ? $prefix.'/' : $prefix.'/'.$path;
    }
}
