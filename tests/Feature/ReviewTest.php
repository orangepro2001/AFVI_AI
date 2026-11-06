<?php

namespace Tests\Feature;

use Tests\TestCase;

class ReviewTest extends TestCase
{
    private string $directory;

    protected function setUp(): void
    {
        parent::setUp();
        $this->directory = sys_get_temp_dir().'/afvi-review-'.bin2hex(random_bytes(8));
        mkdir($this->directory.'/reports/review/current', 0770, true);
        config(['afvi.data_home' => $this->directory, 'afvi.review_run' => 'current']);
    }

    protected function tearDown(): void
    {
        foreach (glob($this->directory.'/reports/review/current/*') ?: [] as $file) {
            unlink($file);
        }
        rmdir($this->directory.'/reports/review/current');
        rmdir($this->directory.'/reports/review');
        rmdir($this->directory.'/reports');
        rmdir($this->directory);
        parent::tearDown();
    }

    private function writeLists(): void
    {
        file_put_contents($this->directory.'/reports/review/current/summary.json', json_encode([
            'generated_at' => '2026-11-06T06:00:00+00:00',
            'model' => ['file' => 'afvi-risk-v4.json', 'version' => 'structured-logistic-v3', 'feature_set' => 'structured_code'],
            'family_evidence' => [
                '7M99-586' => ['heldout_roc_auc' => 0.834],
                '4SP0223G01' => ['heldout_roc_auc' => 0.59],
            ],
            'lots' => [
                ['lot' => 'RH-NEW', 'file' => 'RH-NEW.csv', 'families' => ['7M99-586'], 'first_inspected_at' => '2026-11-07T08:00:00+09:00', 'scored_points' => 3, 'vrs_keyed_points' => 0, 'lot_in_model_training' => false],
                ['lot' => 'B-LOT', 'file' => 'B-LOT.csv', 'families' => ['4SP0223G01'], 'first_inspected_at' => '2026-09-01T08:00:00+09:00', 'scored_points' => 1, 'vrs_keyed_points' => 1, 'lot_in_model_training' => true],
            ],
        ], JSON_THROW_ON_ERROR));
        $header = 'rank,score,top_percent,machine,model_family,result_group,strip,side,unit_idx,img_merge_idx,image_ref,result_data_image,image_saved,area_total,length_max,width_max,light_id,sr_metal,is_dummy,vrs_code,family_in_training,family_heldout_roc_auc,family_heldout_recall_at_30';
        file_put_contents($this->directory.'/reports/review/current/RH-NEW.csv', "\xEF\xBB\xBF".$header."\n"
            ."1,0.912345,33.33,AFVI13,7M99-586,7M99-586/RH-NEW/1,0042,FC,7,3,,ResultData/Data/AFVI13/7M99-586/RH-NEW/1/0042/Unit/U7/FC/p3.bmp,,120,9.5,4.1,0,2,False,,yes,0.834,0.857\n"
            ."2,0.500000,66.67,AFVI13,7M99-586,7M99-586/RH-NEW/1,0042,BC,8,1,,ResultData/Data/AFVI13/7M99-586/RH-NEW/1/0042/Unit/U8/BC/p1.bmp,,40,3.0,2.0,2,1,False,,yes,0.834,0.857\n"
            ."3,0.100000,100.00,AFVI13,7M99-586,7M99-586/RH-NEW/1,0042,FC,-1,9,,ResultData/Data/AFVI13/7M99-586/RH-NEW/1/0042/Dummy/FC/p9.bmp,,35,2.0,2.0,1,2,True,,yes,0.834,0.857\n");
        file_put_contents($this->directory.'/reports/review/current/B-LOT.csv', "\xEF\xBB\xBF".$header."\n"
            ."1,0.700000,100.00,AFVI14,4SP0223G01,4SP0223G01/B-LOT/1,0001,FC,0,1,,ResultData/Data/AFVI14/4SP0223G01/B-LOT/1/0001/Unit/U0/FC/p1.bmp,,10,1.0,1.0,1,2,False,850508,yes,0.590,0.411\n");
    }

    public function test_index_lists_lots_newest_first_with_trust_levels(): void
    {
        $this->writeLists();

        $response = $this->get('/review')->assertOk();
        $response->assertSeeInOrder(['RH-NEW', 'B-LOT']);
        $response->assertSee('留出 LOT ROC AUC 0.834', false);
        $response->assertSee('参与过训练（排序偏乐观）');
        $response->assertSee('/afvi_ai/review/RH-NEW', false);
    }

    public function test_show_ranks_points_and_filters_side_and_dummy(): void
    {
        $this->writeLists();

        $this->get('/review/RH-NEW')
            ->assertOk()
            ->assertSeeInOrder(['0.912', '0.500', '0.100'])
            ->assertSee('ResultData/Data/AFVI13/7M99-586/RH-NEW/1/0042/Unit/U7/FC/p3.bmp')
            ->assertSee('模型可信度 强');

        $this->get('/review/RH-NEW?side=BC')->assertOk()->assertSee('0.500')->assertDontSee('0.912');
        $this->get('/review/RH-NEW?hide_dummy=1')->assertOk()->assertSee('0.912')->assertDontSee('Dummy/FC/p9.bmp');
    }

    public function test_weak_family_and_trained_lot_show_warnings(): void
    {
        $this->writeLists();

        $this->get('/review/B-LOT')
            ->assertOk()
            ->assertSee('该型号模型可信度弱')
            ->assertSee('参与过模型训练')
            ->assertSee('850508');
    }

    public function test_prefixed_routes_and_download_work(): void
    {
        $this->writeLists();

        $this->get('/afvi_ai/review')->assertOk()->assertSee('RH-NEW');
        $download = $this->get('/review/RH-NEW/csv')->assertOk();
        $this->assertStringContainsString('attachment; filename=RH-NEW-review.csv', (string) $download->headers->get('content-disposition'));
    }

    public function test_unknown_lot_and_missing_lists_are_handled(): void
    {
        $this->get('/review')->assertOk()->assertSee('还没有复核清单');
        $this->writeLists();
        $this->get('/review/NOT-LISTED')->assertNotFound();
        $this->get('/review/..%2Fsecret')->assertNotFound();
    }
}
