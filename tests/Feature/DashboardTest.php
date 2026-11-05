<?php

namespace Tests\Feature;

use Tests\TestCase;

class DashboardTest extends TestCase
{
    public function test_missing_report_shows_waiting_state(): void
    {
        $directory = $this->temporaryDirectory();
        config(['afvi.data_home' => $directory]);

        $this->get('/')
            ->assertOk()
            ->assertSee('尚无报告')
            ->assertSee('训练评估完成后，报告将显示在此页面。');

        $this->removeDirectory($directory);
    }

    public function test_malformed_report_is_handled_without_exposing_an_exception(): void
    {
        $directory = $this->temporaryDirectory();
        mkdir($directory.'/reports', 0770, true);
        file_put_contents($directory.'/reports/latest.json', '{broken');
        config(['afvi.data_home' => $directory]);

        $this->get('/')
            ->assertOk()
            ->assertSee('报告格式无效')
            ->assertDontSee('JsonException');

        $this->removeDirectory($directory);
    }

    public function test_valid_report_renders_coverage_validation_and_ranked_evidence(): void
    {
        $directory = $this->temporaryDirectory();
        mkdir($directory.'/reports', 0770, true);
        file_put_contents($directory.'/reports/latest.json', json_encode([
            'schema_version' => 1,
            'generated_at' => '2026-11-06T09:30:00+09:00',
            'data_source' => [
                'export' => 'merge-points.jsonl',
                'manifest' => 'jpg-manifest.jsonl',
                'machines' => 'AFVI13',
            ],
            'historical_vrs_only' => true,
            'scope' => 'Historical VRS labels for detected points',
            'coverage' => [
                'samples' => 26757,
                'lots' => 1,
                'observed' => 26022,
                'unlabeled' => 735,
                'positive' => 1976,
                'assumed_good' => 24046,
                'conflicting' => 0,
                'images' => [
                    'mapped' => 26066,
                    'available' => 0,
                    'missing_manifest_entry' => 691,
                ],
            ],
            'evaluation' => [
                'status' => 'insufficient_support',
                'reason' => 'Only one independent LOT is available.',
                'split' => [
                    'cutoff' => '2026-10-01T00:00:00+09:00',
                    'train_lots' => ['D07834026H3112'],
                    'test_lots' => [],
                    'excluded_lots' => [],
                    'train_rows' => 26022,
                    'test_rows' => 0,
                    'train_classes' => ['0' => 24046, '1' => 1976],
                    'test_classes' => [],
                    'train_class_lots' => ['positive' => 1, 'assumed_good' => 1],
                    'test_class_lots' => [],
                ],
                'metrics' => null,
                'bootstrap' => ['method' => 'lot bootstrap', 'replicates' => 0, 'metrics' => []],
            ],
            'model' => [
                'status' => 'exploratory_only',
                'name' => 'AFVI structured baseline',
                'version' => 'structured-logistic-v1',
                'feature_allowlist' => ['log_area_total', 'log_length_max'],
                'coefficients' => [],
                'threshold_policy' => 'ranking_only',
            ],
            'samples' => [[
                'key' => [
                    'machine' => 'AFVI13',
                    'result_group' => '25FBO015-15/D07834026H3112/3',
                    'strip' => '0001',
                    'side' => 'FC',
                    'unit_idx' => 17,
                    'img_merge_idx' => 2,
                ],
                'lot' => 'D07834026H3112',
                'score' => 0.842,
                'label_source' => 'vrs_code',
                'label_confidence' => 'high',
                'ivs_1' => 'OT15',
                'prediction_role' => 'exploratory_in_sample',
                'evidence' => ['area_total=91', 'length_max=13.2'],
            ]],
        ], JSON_THROW_ON_ERROR));
        config(['afvi.data_home' => $directory]);

        $this->get('/')
            ->assertOk()
            ->assertSee('26,757')
            ->assertSee('export: merge-points.jsonl')
            ->assertSee('manifest: jpg-manifest.jsonl')
            ->assertSee('machines: AFVI13')
            ->assertSee('独立 LOT')
            ->assertSee('insufficient_support')
            ->assertSee('不显示泛化指标')
            ->assertSee('0.842')
            ->assertSee('探索性样本内')
            ->assertSee('正例所属训练 / 测试 LOT')
            ->assertSee('假设良品所属训练 / 测试 LOT')
            ->assertSee('OT15')
            ->assertSee('area_total=91')
            ->assertSee('AFVI 完全漏检的缺陷');

        $this->removeDirectory($directory);
    }

    public function test_evaluated_report_uses_lot_bootstrap_intervals_for_held_out_scores(): void
    {
        $directory = $this->temporaryDirectory();
        mkdir($directory.'/reports', 0770, true);
        file_put_contents($directory.'/reports/latest.json', json_encode([
            'schema_version' => 1,
            'data_source' => ['export' => 'held-out.jsonl', 'manifest' => 'jpg.jsonl', 'machines' => 'AFVI13'],
            'coverage' => ['samples' => 1, 'observed' => 1, 'positive' => 1],
            'evaluation' => [
                'status' => 'evaluated',
                'reason' => 'LOT-disjoint evaluation.',
                'split' => [
                    'train_lots' => ['TRAIN-A', 'TRAIN-B'],
                    'test_lots' => ['TEST-A', 'TEST-B'],
                    'train_class_lots' => ['positive' => 2, 'assumed_good' => 2],
                    'test_class_lots' => ['positive' => 1, 'assumed_good' => 2],
                ],
                'metrics' => ['roc_auc' => 0.812, 'average_precision' => 0.421],
                'bootstrap' => [
                    'intervals' => [
                        'roc_auc' => ['low' => 0.731, 'high' => 0.884],
                        'average_precision' => ['low' => 0.302, 'high' => 0.517],
                    ],
                ],
                'reference' => [
                    'name' => 'log1p_area_total',
                    'metrics' => ['roc_auc' => 0.655],
                    'paired_bootstrap' => ['roc_auc_difference' => ['low' => 0.041, 'high' => 0.212]],
                ],
                'breakdown' => [
                    'model' => [['name' => '7M99-586', 'rows' => 1200, 'lots' => 4, 'model_roc_auc' => 0.801, 'reference_roc_auc' => 0.702]],
                ],
            ],
            'samples' => [[
                'key' => ['machine' => 'AFVI13', 'result_group' => 'MODEL/LOT/1', 'strip' => '1', 'side' => 'FC'],
                'lot' => 'TEST-A',
                'score' => 0.913,
                'prediction_role' => 'held_out',
            ]],
        ], JSON_THROW_ON_ERROR));
        config(['afvi.data_home' => $directory]);

        $this->get('/')
            ->assertOk()
            ->assertSee('ROC AUC · 95% LOT 区间')
            ->assertSee('0.731 - 0.884')
            ->assertSee('0.302 - 0.517')
            ->assertSee('正例所属训练 / 测试 LOT')
            ->assertSee('2 / 1')
            ->assertSee('假设良品所属训练 / 测试 LOT')
            ->assertSee('2 / 2')
            ->assertSee('留出 LOT')
            ->assertSee('0.913')
            ->assertSee('0.655')
            ->assertSee('0.041 - 0.212')
            ->assertSee('7M99-586')
            ->assertSee('0.801 / 0.702');

        $this->removeDirectory($directory);
    }

    private function temporaryDirectory(): string
    {
        $path = sys_get_temp_dir().'/afvi-dashboard-'.bin2hex(random_bytes(8));
        mkdir($path, 0770, true);

        return $path;
    }

    private function removeDirectory(string $path): void
    {
        foreach (glob($path.'/reports/*') ?: [] as $file) {
            unlink($file);
        }
        if (is_dir($path.'/reports')) {
            rmdir($path.'/reports');
        }
        rmdir($path);
    }
}
