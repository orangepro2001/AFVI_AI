<?php

namespace App\Http\Controllers;

use Illuminate\View\View;
use JsonException;

class DashboardController extends Controller
{
    public function __invoke(): View
    {
        $path = rtrim((string) config('afvi.data_home'), DIRECTORY_SEPARATOR).DIRECTORY_SEPARATOR.'reports'.DIRECTORY_SEPARATOR.'latest.json';
        $report = null;
        $error = 'missing';

        try {
            if (is_file($path) && is_readable($path)) {
                $decoded = json_decode(file_get_contents($path), true, 512, JSON_THROW_ON_ERROR);
                if (is_array($decoded) && ($decoded['schema_version'] ?? null) === 1) {
                    $report = $this->normalize($decoded);
                    $error = null;
                } else {
                    $error = 'invalid';
                }
            }
        } catch (JsonException) {
            $error = 'invalid';
        } catch (\Throwable) {
            $error = 'unavailable';
        }

        return view('welcome', [
            'report' => $report,
            'reportError' => $error,
        ]);
    }

    private function normalize(array $report): array
    {
        $dataSource = $report['data_source'] ?? null;
        if (is_array($dataSource)) {
            $sourceParts = [];
            foreach (['export', 'manifest', 'machines'] as $field) {
                if (isset($dataSource[$field]) && is_scalar($dataSource[$field])) {
                    $sourceParts[] = $field.': '.(string) $dataSource[$field];
                }
            }
            $report['data_source'] = $sourceParts === [] ? 'PxSpecTool / VRS' : implode(' · ', $sourceParts);
        } elseif (! is_string($dataSource)) {
            $report['data_source'] = 'PxSpecTool / VRS';
        }

        $coverage = is_array($report['coverage'] ?? null) ? $report['coverage'] : [];
        $images = is_array($coverage['images'] ?? null) ? $coverage['images'] : [];
        $evaluation = is_array($report['evaluation'] ?? null) ? $report['evaluation'] : [];
        $split = is_array($evaluation['split'] ?? null) ? $evaluation['split'] : [];
        $model = is_array($report['model'] ?? null) ? $report['model'] : [];
        $samples = is_array($report['samples'] ?? null) ? $report['samples'] : [];

        $report['coverage'] = [
            'samples' => $this->count($coverage['samples'] ?? null),
            'lots' => $this->count($coverage['lots'] ?? null),
            'observed' => $this->count($coverage['observed'] ?? null),
            'unlabeled' => $this->count($coverage['unlabeled'] ?? null),
            'positive' => $this->count($coverage['positive'] ?? null),
            'assumed_good' => $this->count($coverage['assumed_good'] ?? null),
            'conflicting' => $this->count($coverage['conflicting'] ?? null),
            'images' => [
                'mapped' => $this->count($images['mapped'] ?? null),
                'available' => $this->count($images['available'] ?? null),
                'missing_manifest_entry' => $this->count($images['missing_manifest_entry'] ?? null),
                'missing_file' => $this->count($images['missing_file'] ?? null),
                'manifest_conflict' => $this->count($images['manifest_conflict'] ?? null),
                'not_checked' => $this->count($images['not_checked'] ?? null),
            ],
        ];
        $evaluation['split'] = [
            'cutoff' => $split['cutoff'] ?? null,
            'train_lots' => is_array($split['train_lots'] ?? null) ? $split['train_lots'] : [],
            'test_lots' => is_array($split['test_lots'] ?? null) ? $split['test_lots'] : [],
            'excluded_lots' => is_array($split['excluded_lots'] ?? null) ? $split['excluded_lots'] : [],
            'train_rows' => $this->count($split['train_rows'] ?? null),
            'test_rows' => $this->count($split['test_rows'] ?? null),
            'train_classes' => is_array($split['train_classes'] ?? null) ? $split['train_classes'] : [],
            'test_classes' => is_array($split['test_classes'] ?? null) ? $split['test_classes'] : [],
            'train_class_lots' => $this->classLotCounts($split['train_class_lots'] ?? null),
            'test_class_lots' => $this->classLotCounts($split['test_class_lots'] ?? null),
        ];
        $evaluation['metrics'] = is_array($evaluation['metrics'] ?? null) ? $evaluation['metrics'] : null;
        $evaluation['bootstrap'] = is_array($evaluation['bootstrap'] ?? null) ? $evaluation['bootstrap'] : [];
        $evaluation['sensitivity'] = is_array($evaluation['sensitivity'] ?? null) ? $evaluation['sensitivity'] : [];
        $report['evaluation'] = $evaluation;
        $model['feature_allowlist'] = is_array($model['feature_allowlist'] ?? null) ? $model['feature_allowlist'] : [];
        $report['model'] = $model;

        $normalizedSamples = [];
        foreach (array_slice($samples, 0, 100) as $sample) {
            if (! is_array($sample)) {
                continue;
            }

            if (! isset($sample['score']) || ! is_numeric($sample['score'])) {
                continue;
            }
            $sample['key'] = is_array($sample['key'] ?? null) ? $sample['key'] : [];
            $sample['evidence'] = is_array($sample['evidence'] ?? null) || is_string($sample['evidence'] ?? null)
                ? $sample['evidence']
                : [];
            $sample['prediction_role'] = in_array($sample['prediction_role'] ?? null, ['held_out', 'exploratory_in_sample'], true)
                ? $sample['prediction_role']
                : 'unknown';
            $normalizedSamples[] = $sample;
        }
        $report['samples'] = $normalizedSamples;

        return $report;
    }

    private function count(mixed $value): int
    {
        return is_numeric($value) && (int) $value >= 0 ? (int) $value : 0;
    }

    private function classLotCounts(mixed $value): ?array
    {
        if (! is_array($value) || ! array_intersect(['positive', 'assumed_good'], array_keys($value))) {
            return null;
        }

        return [
            'positive' => $this->count($value['positive'] ?? null),
            'assumed_good' => $this->count($value['assumed_good'] ?? null),
        ];
    }
}
