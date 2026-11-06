<?php

namespace Tests\Feature;

use Illuminate\Http\UploadedFile;
use Illuminate\Support\Str;
use Ramsey\Uuid\Uuid;
use Tests\TestCase;

class VisionTest extends TestCase
{
    private string $directory;

    protected function setUp(): void
    {
        parent::setUp();
        $this->directory = sys_get_temp_dir().'/afvi-vision-'.bin2hex(random_bytes(8));
        mkdir($this->directory.'/queue/outbox', 0770, true);
        mkdir($this->directory.'/queue/inbox', 0770, true);
        mkdir($this->directory.'/reports/vision', 0770, true);
        config([
            'afvi.data_home' => $this->directory,
            'afvi.vision_queue' => $this->directory.'/queue',
            'afvi.vision_timeout_seconds' => 0.3,
        ]);
    }

    protected function tearDown(): void
    {
        Str::createUuidsNormally();
        foreach (['queue/outbox', 'queue/inbox', 'queue', 'reports/vision', 'reports', ''] as $sub) {
            foreach (glob($this->directory.'/'.$sub.'/*.*') ?: [] as $file) {
                unlink($file);
            }
        }
        foreach (['queue/outbox', 'queue/inbox', 'queue', 'reports/vision', 'reports', ''] as $sub) {
            @rmdir(rtrim($this->directory.'/'.$sub, '/'));
        }
        parent::tearDown();
    }

    /**
     * A real 1x1 JPEG, so MIME detection works without the GD extension.
     */
    private function image(): UploadedFile
    {
        $path = $this->directory.'/upload-'.bin2hex(random_bytes(4)).'.jpg';
        file_put_contents($path, base64_decode('/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA='));

        return new UploadedFile($path, 'p1.jpg', 'image/jpeg', null, true);
    }

    public function test_page_shows_form_and_model_metrics(): void
    {
        file_put_contents($this->directory.'/reports/vision/latest.json', json_encode([
            'model' => ['architecture' => 'resnet18_imagenet_finetuned', 'dataset' => ['images' => 100000]],
            'held_out' => [
                'lots' => 28,
                'images' => 40000,
                'metrics' => ['roc_auc' => 0.861],
                'bootstrap' => ['intervals' => ['roc_auc' => ['low' => 0.82, 'high' => 0.9]]],
                'at_threshold' => ['keyed_miss_rate' => 0.21, 'good_false_alarm_rate' => 0.2],
                'by_family' => [['family' => '7M99-586', 'roc_auc' => 0.88]],
            ],
        ], JSON_THROW_ON_ERROR));

        $this->get('/vision')
            ->assertOk()
            ->assertSee('上传图片')
            ->assertSee('0.861 (0.820–0.900)', false)
            ->assertSee('21.0%')
            ->assertSee('7M99-586 0.88')
            ->assertSee('action="/afvi_ai/vision"', false);
    }

    public function test_upload_returns_worker_verdict(): void
    {
        $id = '11111111-2222-4333-8444-555555555555';
        Str::createUuidsUsing(fn () => Uuid::fromString($id));
        file_put_contents($this->directory.'/queue/outbox/'.$id.'.json', json_encode(['status' => 'ok', 'score' => 0.83, 'threshold' => 0.41, 'verdict' => 'defect']));

        $this->post('/vision', ['image' => $this->image()], ['Origin' => 'http://localhost'])
            ->assertOk()
            ->assertSee('倾向不良')
            ->assertSee('0.830')
            ->assertSee('data:image/jpeg;base64,', false);
        $this->assertFileExists($this->directory.'/queue/inbox/'.$id.'.img');
        $this->assertFileDoesNotExist($this->directory.'/queue/outbox/'.$id.'.json');
    }

    public function test_upload_without_worker_times_out_and_cleans_inbox(): void
    {
        $this->post('/vision', ['image' => $this->image()], ['Origin' => 'http://localhost'])
            ->assertOk()
            ->assertSee('推理服务没有在时限内返回结果');
        $this->assertSame([], glob($this->directory.'/queue/inbox/*.img'));
    }

    public function test_cross_origin_and_invalid_uploads_are_rejected(): void
    {
        $this->post('/vision', ['image' => $this->image()], ['Origin' => 'https://evil.example'])->assertForbidden();
        $this->post('/vision', ['image' => $this->image()])->assertForbidden();
        $this->post('/vision', ['image' => UploadedFile::fake()->create('notes.txt', 3, 'text/plain')], ['Origin' => 'http://localhost'])
            ->assertSessionHasErrors('image');
    }

    public function test_worker_error_is_shown(): void
    {
        $id = '99999999-2222-4333-8444-555555555555';
        Str::createUuidsUsing(fn () => Uuid::fromString($id));
        file_put_contents($this->directory.'/queue/outbox/'.$id.'.json', json_encode(['status' => 'error', 'error' => 'cannot identify image file']));

        $this->post('/vision', ['image' => $this->image()], ['Origin' => 'http://localhost'])
            ->assertOk()
            ->assertSee('图片无法判断：cannot identify image file');
    }
}
