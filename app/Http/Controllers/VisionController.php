<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use Illuminate\Support\Str;
use Illuminate\View\View;
use JsonException;

class VisionController extends Controller
{
    public function show(): View
    {
        return view('vision.show', ['report' => $this->report(), 'worker' => $this->worker(), 'result' => null, 'error' => null, 'preview' => null]);
    }

    public function predict(Request $request): View
    {
        if (! $this->sameOrigin($request)) {
            abort(403, 'Cross-origin upload rejected.');
        }
        $validated = $request->validate([
            'image' => ['required', 'file', 'max:2048', 'mimetypes:image/bmp,image/x-ms-bmp,image/x-bmp,image/jpeg,image/png'],
        ]);
        $file = $validated['image'];
        $bytes = (string) file_get_contents($file->getRealPath());
        $preview = 'data:'.$file->getMimeType().';base64,'.base64_encode($bytes);

        $queue = rtrim((string) config('afvi.vision_queue'), DIRECTORY_SEPARATOR);
        $inbox = $queue.'/inbox';
        $outbox = $queue.'/outbox';
        foreach ([$inbox, $outbox] as $directory) {
            if (! is_dir($directory)) {
                mkdir($directory, 0775, true);
            }
        }
        $id = (string) Str::uuid();
        $temporary = $inbox.'/'.$id.'.tmp';
        file_put_contents($temporary, $bytes);
        rename($temporary, $inbox.'/'.$id.'.img');

        $resultPath = $outbox.'/'.$id.'.json';
        $deadline = microtime(true) + (float) config('afvi.vision_timeout_seconds', 15);
        $result = null;
        while (microtime(true) < $deadline) {
            clearstatcache(true, $resultPath);
            if (is_file($resultPath)) {
                try {
                    $result = json_decode((string) file_get_contents($resultPath), true, 32, JSON_THROW_ON_ERROR);
                } catch (JsonException) {
                    $result = ['status' => 'error', 'error' => 'invalid worker response'];
                }
                @unlink($resultPath);
                break;
            }
            usleep(100_000);
        }

        $error = null;
        if ($result === null) {
            @unlink($inbox.'/'.$id.'.img');
            $error = '推理服务没有在时限内返回结果，请稍后再试或联系管理员检查 afvi-vision-worker 服务。';
        } elseif (($result['status'] ?? null) !== 'ok') {
            $error = '图片无法判断：'.(is_string($result['error'] ?? null) ? $result['error'] : '未知错误');
            $result = null;
        }

        return view('vision.show', [
            'report' => $this->report(),
            'worker' => $this->worker(),
            'result' => $result,
            'error' => $error,
            'preview' => $preview,
            'filename' => $file->getClientOriginalName(),
        ]);
    }

    private function sameOrigin(Request $request): bool
    {
        $source = $request->headers->get('Origin') ?: $request->headers->get('Referer');
        if (! is_string($source) || $source === '') {
            return false;
        }
        $sourceHost = parse_url($source, PHP_URL_HOST);
        $forwardedHost = $request->headers->get('X-Forwarded-Host');
        $hosts = array_filter([$request->getHost(), is_string($forwardedHost) ? explode(':', $forwardedHost)[0] : null]);

        return is_string($sourceHost) && in_array($sourceHost, $hosts, true);
    }

    /**
     * @return array<string, mixed>|null
     */
    private function report(): ?array
    {
        $path = rtrim((string) config('afvi.data_home'), DIRECTORY_SEPARATOR).'/reports/vision/latest.json';
        if (! is_file($path) || ! is_readable($path)) {
            return null;
        }
        try {
            $decoded = json_decode((string) file_get_contents($path), true, 512, JSON_THROW_ON_ERROR);
        } catch (JsonException) {
            return null;
        }

        return is_array($decoded) ? $decoded : null;
    }

    /**
     * @return array<string, mixed>|null
     */
    private function worker(): ?array
    {
        $path = rtrim((string) config('afvi.vision_queue'), DIRECTORY_SEPARATOR).'/worker.json';
        if (! is_file($path)) {
            return null;
        }
        try {
            $decoded = json_decode((string) file_get_contents($path), true, 8, JSON_THROW_ON_ERROR);
        } catch (JsonException) {
            return null;
        }

        return is_array($decoded) ? $decoded : null;
    }
}
