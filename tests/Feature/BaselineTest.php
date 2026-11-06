<?php

namespace Tests\Feature;

use Tests\TestCase;

class BaselineTest extends TestCase
{
    private string $directory;

    protected function setUp(): void
    {
        parent::setUp();
        $this->directory = sys_get_temp_dir().'/afvi-baseline-'.bin2hex(random_bytes(8));
        mkdir($this->directory.'/reports/baseline/BM-model', 0770, true);
        config(['afvi.data_home' => $this->directory]);
    }

    protected function tearDown(): void
    {
        foreach ([$this->directory.'/reports/baseline/BM-model', $this->directory.'/reports/baseline'] as $folder) {
            foreach (glob($folder.'/*.json') ?: [] as $file) {
                unlink($file);
            }
        }
        rmdir($this->directory.'/reports/baseline/BM-model');
        rmdir($this->directory.'/reports/baseline');
        rmdir($this->directory.'/reports');
        rmdir($this->directory);
        parent::tearDown();
    }

    private function write(string $name, array $data): void
    {
        file_put_contents($this->directory.'/reports/baseline/'.$name, json_encode($data, JSON_THROW_ON_ERROR));
    }

    private function fixture(): void
    {
        $this->write('index.json', [
            'generated_at' => '2026-11-07T02:05:00+09:00',
            'para_url' => '/afvi/para/',
            'consensus_share' => 0.6,
            'min_consensus_models' => 5,
            'visions' => [['name' => 'BM', 'side' => 'BOTTOM', 'models' => 60, 'lights' => ['0', '2']]],
        ]);
        $row = fn (string $node, string $key, string $channel, float $mode, float $share, int $active) => [
            'node' => $node, 'node_name' => 'Unit / NonMetal / Space1', 'in_tree' => true, 'tag' => 'INSPECTION', 'key' => $key,
            'name_en' => $key === '1002' ? 'Bright Defect Size(Pixel)' : 'Dark Defect(TH)', 'name_ko' => $key === '1002' ? '밝은 불량 크기' : '',
            'channel' => $channel, 'models' => 60, 'enabled' => 40, 'active' => $active, 'inactive_share' => 0.25, 'default' => 30.0,
            'stats' => ['mode' => $mode, 'mode_share' => $share, 'median' => $mode, 'p10' => 20.0, 'p90' => 50.0, 'min' => 10.0, 'max' => 90.0, 'distinct' => 4, 'top_values' => [[$mode, (int) round($share * $active)], [50.0, 3]]],
        ];
        $this->write('BM-L2.json', ['vision' => 'BM', 'light' => '2', 'rows' => [
            $row('1-5-55', '1002', 'B', 30.0, 0.8, 30),
            $row('1-5-55', '1009', 'B', 40.0, 0.4, 30),
        ]]);
        $this->write('BM-L0.json', ['vision' => 'BM', 'light' => '0', 'rows' => []]);
        $this->write('BM-models.json', ['vision' => 'BM', 'models' => ['7M99-586-00', '25FBO015-15-00'], 'deviations' => []]);
        $this->write('BM-model/7M99-586-00.json', ['model' => '7M99-586-00', 'lights' => ['2' => [
            '1-5-55|INSPECTION|1002|B' => [50.0, true],
            '1-5-55|INSPECTION|1009|B' => [40.0, true],
        ]]]);
        $this->write('ai-evidence.json', ['7M99-586' => [
            ['kind' => 'limit_change', 'side' => 'BC', 'light' => '2', 'channel' => '3', 'sr_metal' => '1', 'code_main' => '10510',
                'regimes' => [['limit' => 35, 'lots' => 16, 'first_date' => '2026-06-08', 'last_date' => '2026-07-29'], ['limit' => 50, 'lots' => 2, 'first_date' => '2026-06-02', 'last_date' => '2026-06-04']],
                'changes' => [['low_limit' => 35, 'high_limit' => 50, 'point_share_below' => 0.285, 'keyed_share_below' => 0.099, 'keyed_rate_below' => 0.0028, 'keyed_rate_above' => 0.0102]]],
            ['kind' => 'draft', 'side' => 'FC', 'light' => '2', 'channel' => '3', 'sr_metal' => '1', 'recommendation' => 'candidate', 'recommended_min_area' => '43', 'observed_min_area' => '20'],
        ]]);
    }

    public function test_baseline_lists_majority_values_and_filters(): void
    {
        $this->fixture();

        $this->get('/baseline?vision=BM&light=2')
            ->assertOk()
            ->assertSee('Bright Defect Size(Pixel)')
            ->assertSee('밝은 불량 크기')
            ->assertSee('80%')
            ->assertSee('7M99-586-00');
        $this->get('/afvi_ai/baseline?vision=BM&light=2&view=split')->assertOk()->assertSee('Dark Defect(TH)')->assertDontSee('Bright Defect Size(Pixel)');
        $this->get('/baseline?vision=BM&light=2&q=1002')->assertOk()->assertSee('Bright Defect Size(Pixel)')->assertDontSee('Dark Defect(TH)');
    }

    public function test_model_page_shows_differences_ai_evidence_and_para_link(): void
    {
        $this->fixture();

        $this->get('/baseline/model?vision=BM&model=7M99-586-00&light=2')
            ->assertOk()
            ->assertSee('和多数人不一样：1 处')
            ->assertSee('Bright Defect Size(Pixel)')
            ->assertDontSee('Dark Defect(TH)')
            ->assertSee('35（16 LOT，2026-06-08~2026-07-29）', false)
            ->assertSee('打码点 9.9%')
            ->assertDontSee('建议 43')
            ->assertSee('/afvi/para/#BM/7M99-586-00/2', false);

        $this->get('/baseline/model?vision=BM&model=7M99-586-00&light=2&all=1')->assertOk()->assertSee('Dark Defect(TH)');
    }

    public function test_unknown_model_and_missing_baseline(): void
    {
        $this->get('/baseline')->assertOk()->assertSee('还没有参数基准');
        $this->fixture();
        $this->get('/baseline/model?vision=BM&model=../secret&light=2')->assertNotFound();
        $this->get('/baseline/model?vision=BM&model=NOPE-00&light=2')->assertNotFound();
    }
}
